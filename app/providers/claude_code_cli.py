"""Claude Code CLI (`claude -p --output-format json`) — исполнение через
подписку Claude Max/Pro, а не через метрируемый ANTHROPIC_API_KEY (это
делает app.providers.claude.ClaudeProvider, через Anthropic SDK).

Произвольно много аккаунтов используют один и тот же бинарник
(CLAUDE_CLI_PATH) с разным CLAUDE_CODE_OAUTH_TOKEN в окружении
процесса-потомка на каждую попытку:
- основной слот (.env/🔑 Ключ) без токена — CLI сам берёт обычную
  интерактивную сессию `claude` на этой машине
  (~/.claude/.credentials.json — создаётся `claude login`/первым запуском
  `claude`), с токеном — переопределяет её;
- дополнительные аккаунты ("➕ Добавить ещё аккаунт", см.
  app.providers.accounts_store) ВСЕГДА требуют токен (см. `claude
  setup-token`, выполняется ОДИН РАЗ человеком в настоящем терминале — сама
  команда рисует интерактивный TUI и не работает через subprocess без TTY,
  так что бот её не запускает) — второй локальной сессии не бывает.
Перебор при ошибке/квоте — см. app.providers.multi_account.

auth_status() никогда не запускает сам claude — это стоило бы реальных
денег на каждый рендер ⚙️ Настроек (каждое нажатие кнопки в боте). Для
токен-аккаунтов — просто "токен задан" (тот же принцип, что у остальных
API-key провайдеров: не проверяем, просто доверяем настройке). Для
сессии-по-умолчанию — только факт наличия локального credentials-файла."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
from pathlib import Path

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderQuotaExceededError,
    ProviderResult,
    RunOptions,
)
from app.providers.multi_account import run_with_account_fallback
from app.providers.quota import QuotaTracker

# У `claude -p` нет структурированного кода ошибки типа HTTP 429 в JSON
# (см. is_error/result) — эвристика по тексту, тот же приём, что и в
# app.providers.cursor для cursor-agent.
QUOTA_ERROR_MARKERS = ("rate limit", "rate-limit", "429", "quota", "usage limit", "too many requests")

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

# Fleet-checkers/критики (см. app.tasks.protocol_full) гоняют до 4
# параллельных run_prompt через ОДИН provider — при локальной сессии (без
# CLAUDE_CODE_OAUTH_TOKEN) все они читают/обновляют один и тот же
# ~/.claude/.credentials.json. Конкурентный рефреш access-токена гонит один
# процесс мимо только что провёрнутого другим — ловили спорадические 401
# Invalid bearer token именно на этом шаге. Сериализуем вызовы, которые
# используют локальную сессию (аккаунты по явному токену не делят файл —
# им лок не нужен).
_LOCAL_SESSION_LOCK = threading.Lock()


def _looks_like_quota_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in QUOTA_ERROR_MARKERS)


def _local_session_exists() -> bool:
    return CREDENTIALS_PATH.exists()


# Сентинел для "основной слот, но без токена — бери локальную сессию
# claude на этой машине" в списке credentials: None/"" неотличимы от
# "аккаунт не настроен" для generic-перебора, а тут это ВАЛИДНЫЙ, рабочий
# аккаунт (пока файл сессии существует) — единственный такой случай среди
# всех multi-account провайдеров, см. модульный докстринг.
_LOCAL_SESSION = "\0local-session"


class ClaudeCodeCliProvider(AIProvider):
    name = ProviderName.CLAUDE_CODE

    def __init__(
        self,
        cli_path: str | None,
        oauth_token: str | None = None,
        quota_tracker: QuotaTracker | None = None,
        *,
        extra_accounts: list[str] | None = None,
    ) -> None:
        self._cli_path = cli_path
        self._oauth_token = oauth_token
        self._extra_accounts = list(extra_accounts or [])
        self._quota_tracker = quota_tracker or QuotaTracker(ProviderName.CLAUDE_CODE)

    def _all_credentials(self) -> list[str]:
        primary: list[str] = []
        if self._oauth_token:
            primary.append(self._oauth_token)
        elif _local_session_exists():
            primary.append(_LOCAL_SESSION)
        return primary + self._extra_accounts

    def _labeled_credentials(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self._oauth_token:
            pairs.append(("primary", self._oauth_token))
        elif _local_session_exists():
            pairs.append(("primary", _LOCAL_SESSION))
        pairs += [(f"extra:{i}", s) for i, s in enumerate(self._extra_accounts, start=1)]
        return pairs

    def supports_key_entry(self) -> bool:
        return True

    def update_api_key(self, api_key: str | None) -> None:
        self._oauth_token = api_key

    def set_extra_accounts(self, extra_accounts: list[str]) -> None:
        """Живое обновление списка доп. аккаунтов, без рестарта процесса —
        тот же принцип, что у update_api_key."""
        self._extra_accounts = list(extra_accounts)

    def auth_status(self) -> AuthStatus:
        if not self._cli_path:
            return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="CLAUDE_CLI_PATH не задан")
        credentials = self._all_credentials()
        if credentials:
            if len(credentials) > 1:
                detail = f"{len(credentials)} аккаунта(ов)"
            elif credentials[0] == _LOCAL_SESSION:
                detail = "локальная сессия claude"
            else:
                detail = "отдельный токен-аккаунт"
            return AuthStatus(status=ProviderAccountStatus.CONNECTED, detail=detail)
        return AuthStatus(
            status=ProviderAccountStatus.NOT_CONNECTED,
            detail="не залогинен — запусти `claude` (или `claude setup-token` + 🔑 Ключ) в терминале",
        )

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        if not self._cli_path:
            raise ProviderNotAuthenticatedError(
                f"CLAUDE_CLI_PATH не задан — некуда запускать {self.name.value}."
            )
        options = options or RunOptions()
        return run_with_account_fallback(
            self._labeled_credentials(),
            lambda label, credential: self._run_once(credential, prompt, options, account_label=label),
            not_configured_hint=(
                f"{self.name.value}: не залогинен — запусти `claude` или `claude setup-token` "
                "в терминале на этой машине."
            ),
        )

    def _run_once(
        self, credential: str, prompt: str, options: RunOptions, *, account_label: str | None = None
    ) -> ProviderResult:
        # Промпт — через stdin, не как аргумент командной строки: Windows
        # ограничивает длину командной строки процесса (~32K символов), а
        # промпты Full ЧЕК (отчёты, найденные проблемы) легко превышают
        # это — раньше падало OSError [WinError 206] "имя файла или его
        # расширение имеет слишком большую длину".
        args = [self._cli_path, "-p", "--output-format", "json"]
        if options.system:
            args += ["--system-prompt", options.system]
        if options.model:
            args += ["--model", options.model]

        env = dict(os.environ)
        if credential != _LOCAL_SESSION:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = credential
        else:
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

        lock = _LOCAL_SESSION_LOCK if credential == _LOCAL_SESSION else contextlib.nullcontext()
        try:
            with lock:
                result = subprocess.run(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                    env=env,
                )
        except subprocess.TimeoutExpired as exc:
            # claude -p не всегда отдаёт чистую 429-ошибку на реальном
            # лимите подписки — иногда просто зависает без ответа до
            # таймаута (подтверждено вживую: аккаунт был на лимите, CLI
            # молчал все 600с вместо быстрой ошибки). Раньше это уходило
            # generic ProviderError, HANDOVER-пауза не срабатывала — job
            # падал насовсем вместо того, чтобы подождать сброса лимита.
            raise ProviderQuotaExceededError(
                f"{self.name.value}: похоже на лимит (CLI завис без ответа {exc.timeout}с): {exc}"
            ) from exc
        except OSError as exc:
            raise ProviderError(f"{self.name.value} CLI error: {exc}") from exc

        output = result.stdout.strip()
        if not output:
            error_text = result.stderr.strip() or f"код возврата {result.returncode}"
            if _looks_like_quota_error(error_text):
                raise ProviderQuotaExceededError(f"{self.name.value}: похоже на лимит/квоту: {error_text}")
            raise ProviderError(f"{self.name.value} завершился с кодом {result.returncode}: {error_text}")

        # claude -p пишет JSON в stdout даже при ошибке (returncode != 0) —
        # разбираем его В ЛЮБОМ случае, чтобы достать настоящий
        # api_error_status (429 — надёжный признак лимита) вместо того,
        # чтобы гадать по тексту сырого вывода.
        try:
            data = json.loads(output)
        except ValueError as exc:
            if result.returncode != 0:
                error_text = result.stderr.strip() or output
                if _looks_like_quota_error(error_text):
                    raise ProviderQuotaExceededError(
                        f"{self.name.value}: похоже на лимит/квоту: {error_text}"
                    ) from exc
                raise ProviderError(
                    f"{self.name.value} завершился с кодом {result.returncode}: {error_text}"
                ) from exc
            raise ProviderError(f"{self.name.value}: не удалось разобрать JSON-ответ: {exc}") from exc

        if data.get("is_error") or result.returncode != 0:
            error_text = str(data.get("result") or data)
            if data.get("api_error_status") == 429 or _looks_like_quota_error(error_text):
                raise ProviderQuotaExceededError(f"{self.name.value}: похоже на лимит/квоту: {error_text}")
            raise ProviderError(f"{self.name.value}: {error_text}")

        usage = data.get("usage") or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        self._quota_tracker.record(
            model=None, input_tokens=input_tokens, output_tokens=output_tokens, account_label=account_label
        )

        return ProviderResult(
            text=data.get("result", ""),
            model=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=data,
        )

    def estimate_quota(self):
        return self._quota_tracker.estimate()
