"""Общая база для любого провайдера с OpenAI-совместимым Chat Completions
API (Bearer-токен + POST {base_url}/chat/completions) — Gemini, DeepSeek,
Grok (xAI) и Mistral отдают именно такой контракт, как и локальная LLM
(app.providers.local_llm) и Codex (app.providers.codex, который исторически
не унаследован отсюда, чтобы не трогать уже написанный и протестированный
код без необходимости).

Конкретные провайдеры (см. gemini.py/deepseek.py/grok.py/mistral.py/
openrouter.py) — это только `name`, `base_url`, `_DEFAULT_MODEL` и текст
подсказки в auth_status/run_prompt; вся HTTP/квота/ошибки-логика тут одна."""

from __future__ import annotations

import httpx

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    ProviderError,
    ProviderQuotaExceededError,
    ProviderResult,
    RunOptions,
)
from app.providers.multi_account import run_with_account_fallback
from app.providers.quota import QuotaTracker


class OpenAICompatibleProvider(AIProvider):
    """Базовый класс — конкретные провайдеры задают name/base_url/default_model
    в подклассе и передают человекочитаемое название в __init__ для текстов
    ошибок ("Gemini: превышен лимит запросов" и т.п.).

    Один основной ключ (api_key, .env/🔑 Ключ) + произвольно много
    дополнительных аккаунтов (extra_accounts, "➕ Добавить ещё аккаунт" —
    см. app.providers.accounts_store) — перебираются по порядку, следующий
    пробуется только при ошибке/квоте текущего (см. multi_account)."""

    name: ProviderName
    _base_url: str
    _default_model: str
    _display_name: str

    def __init__(
        self,
        api_key: str | None,
        quota_tracker: QuotaTracker | None = None,
        *,
        model: str | None = None,
        extra_accounts: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._extra_accounts = list(extra_accounts or [])
        self._model = model or self._default_model
        self._quota_tracker = quota_tracker or QuotaTracker(self.name)

    def _all_credentials(self) -> list[str]:
        return ([self._api_key] if self._api_key else []) + self._extra_accounts

    def auth_status(self) -> AuthStatus:
        credentials = self._all_credentials()
        if credentials:
            detail = f"{len(credentials)} аккаунта(ов)" if len(credentials) > 1 else None
            return AuthStatus(status=ProviderAccountStatus.CONNECTED, detail=detail)
        return AuthStatus(
            status=ProviderAccountStatus.NOT_CONNECTED, detail=f"{self._env_var_hint()} не задан"
        )

    def _env_var_hint(self) -> str:
        return f"{self._display_name.upper().replace(' ', '_')}_API_KEY"

    def supports_key_entry(self) -> bool:
        return True

    def update_api_key(self, api_key: str | None) -> None:
        self._api_key = api_key

    def set_extra_accounts(self, extra_accounts: list[str]) -> None:
        """Живое обновление списка доп. аккаунтов — вызывается ботом сразу
        после add_extra_account()/remove_extra_account() в accounts_store,
        без рестарта процесса (тот же принцип, что у update_api_key)."""
        self._extra_accounts = list(extra_accounts)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        options = options or RunOptions()
        return run_with_account_fallback(
            self._all_credentials(),
            lambda api_key: self._run_once(api_key, prompt, options),
            not_configured_hint=(
                f"{self._env_var_hint()} не задан — залогинься в Настройках → 🔌 Провайдеры ИИ."
            ),
        )

    def _run_once(self, api_key: str, prompt: str, options: RunOptions) -> ProviderResult:
        messages = []
        if options.system:
            messages.append({"role": "system", "content": options.system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": options.model or self._model,
                    "messages": messages,
                    "max_tokens": options.max_tokens,
                    "temperature": options.temperature,
                },
                timeout=180,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise ProviderQuotaExceededError(
                    f"{self._display_name}: превышен лимит запросов (429): {exc}"
                ) from exc
            raise ProviderError(f"{self._display_name} API error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self._display_name} network error: {exc}") from exc

        data = response.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        self._quota_tracker.record(
            model=data.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
        return ProviderResult(
            text=choice,
            model=data.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )

    def estimate_quota(self):
        return self._quota_tracker.estimate()
