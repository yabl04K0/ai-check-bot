"""Codex (OpenAI) — API-ключ или логин в аккаунт ChatGPT (CLI, TODO)."""

from __future__ import annotations

import httpx

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderResult,
    RunOptions,
)
from app.providers.quota import QuotaTracker

DEFAULT_MODEL = "gpt-4.1"
CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


class CodexProvider(AIProvider):
    """Вызывает OpenAI Chat Completions API напрямую через httpx.

    Логин через сам codex CLI / ChatGPT-аккаунт (Tier из README) — не
    реализован, это TODO: сейчас поддерживается только режим API-ключа.
    """

    name = ProviderName.CODEX

    def __init__(self, api_key: str | None, quota_tracker: QuotaTracker | None = None) -> None:
        self._api_key = api_key
        self._quota_tracker = quota_tracker or QuotaTracker(ProviderName.CODEX)

    def auth_status(self) -> AuthStatus:
        if self._api_key:
            return AuthStatus(status=ProviderAccountStatus.CONNECTED)
        return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="OPENAI_API_KEY не задан")

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
