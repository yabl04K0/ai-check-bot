"""Codex (OpenAI) — API-ключ ИЛИ CLI-логин в аккаунт ChatGPT.

Выполнение промптов (run_prompt) пока работает только через API-ключ —
Chat Completions API напрямую через httpx. Неинтерактивный вызов
реального `codex` CLI под конкретный промпт (без API-ключа, через
ChatGPT-логин) не реализован: у codex CLI нет стабильного публичного
контракта на non-interactive exec, который можно было бы захардкодить
не рискуя сломаться на следующей версии CLI — см. TODO. login() при этом
УЖЕ реален: кнопка "Войти" в боте прогоняет `<CODEX_CLI_PATH> login`.
"""

from __future__ import annotations

import httpx

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    LoginResult,
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderResult,
    RunOptions,
)
from app.providers.cli_login import run_cli_login
from app.providers.quota import QuotaTracker

DEFAULT_MODEL = "gpt-4.1"
CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


class CodexProvider(AIProvider):
    """Вызывает OpenAI Chat Completions API напрямую через httpx."""

    name = ProviderName.CODEX

    def __init__(
        self,
        api_key: str | None,
        quota_tracker: QuotaTracker | None = None,
        cli_path: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._cli_path = cli_path
        self._quota_tracker = quota_tracker or QuotaTracker(ProviderName.CODEX)

    def auth_status(self) -> AuthStatus:
        if self._api_key:
            return AuthStatus(status=ProviderAccountStatus.CONNECTED)
        if self._cli_path:
            # Реальный статус CLI-логина не проверяем автоматически (нет
            # надёжной non-interactive команды статуса, см. модульный
            # докстринг) — только сообщаем, что CLI настроен.
            return AuthStatus(
                status=ProviderAccountStatus.NOT_CONNECTED,
                detail="CLI настроен, но статус логина не проверяется автоматически — нажми «Войти»",
            )
        return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="OPENAI_API_KEY не задан")

    def supports_login(self) -> bool:
        return bool(self._cli_path)

    def login(self) -> LoginResult:
        return run_cli_login(
            self._cli_path,
            missing_path_hint="CODEX_CLI_PATH не задан в .env — некуда запускать login.",
        )

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        if not self._api_key:
            raise ProviderNotAuthenticatedError(
                "OPENAI_API_KEY не задан — залогинься в Настройках → 🔌 Провайдеры ИИ."
            )
        options = options or RunOptions()
        messages = []
        if options.system:
            messages.append({"role": "system", "content": options.system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": options.model or DEFAULT_MODEL,
                    "messages": messages,
                    "max_tokens": options.max_tokens,
                    "temperature": options.temperature,
                },
                timeout=120,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Codex (OpenAI) API error: {exc}") from exc

        data = response.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        self._quota_tracker.record(
            model=data.get("model"),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
        return ProviderResult(
            text=choice,
            model=data.get("model"),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )

    def estimate_quota(self):
        return self._quota_tracker.estimate()
