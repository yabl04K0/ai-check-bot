"""Claude (Anthropic) — штатный флот-провайдер, полный протокол ЧЕК."""

from __future__ import annotations

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

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


class ClaudeProvider(AIProvider):
    name = ProviderName.CLAUDE

    def __init__(self, api_key: str | None, quota_tracker: QuotaTracker | None = None) -> None:
        self._api_key = api_key
        self._client = None
        self._quota_tracker = quota_tracker or QuotaTracker(ProviderName.CLAUDE)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ProviderNotAuthenticatedError(
                "ANTHROPIC_API_KEY не задан — залогинься в Настройках → 🔌 Провайдеры ИИ."
            )
        import anthropic  # локальный импорт: не тянуть SDK, если провайдер не используется

        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def auth_status(self) -> AuthStatus:
        if self._api_key:
            return AuthStatus(status=ProviderAccountStatus.CONNECTED)
        return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="ANTHROPIC_API_KEY не задан")

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        options = options or RunOptions()
        client = self._get_client()
        try:
            message = client.messages.create(
                model=options.model or DEFAULT_MODEL,
                max_tokens=options.max_tokens,
                temperature=options.temperature,
                system=options.system or "",
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001 — оборачиваем любую ошибку SDK
            raise ProviderError(f"Claude API error: {exc}") from exc

        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
        self._quota_tracker.record(
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
        return ProviderResult(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            raw=message,
        )

    def estimate_quota(self):
        return self._quota_tracker.estimate()
