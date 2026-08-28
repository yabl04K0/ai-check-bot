"""Claude (Anthropic) — штатный флот-провайдер, полный протокол ЧЕК."""

from __future__ import annotations

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    ProviderError,
    ProviderQuotaExceededError,
    ProviderResult,
    RunOptions,
)
from app.providers.multi_account import label_credentials, run_with_account_fallback
from app.providers.quota import QuotaTracker

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


class ClaudeProvider(AIProvider):
    """Один основной ключ (.env/🔑 Ключ) + произвольно много дополнительных
    аккаунтов ("➕ Добавить ещё аккаунт", см. app.providers.accounts_store) —
    перебираются по порядку, следующий пробуется только при ошибке/квоте
    текущего (см. app.providers.multi_account)."""

    name = ProviderName.CLAUDE

    def __init__(
        self,
        api_key: str | None,
        quota_tracker: QuotaTracker | None = None,
        *,
        extra_accounts: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._extra_accounts = list(extra_accounts or [])
        self._quota_tracker = quota_tracker or QuotaTracker(ProviderName.CLAUDE)

    def _all_credentials(self) -> list[str]:
        return ([self._api_key] if self._api_key else []) + self._extra_accounts

    def auth_status(self) -> AuthStatus:
        credentials = self._all_credentials()
        if credentials:
            detail = f"{len(credentials)} аккаунта(ов)" if len(credentials) > 1 else None
            return AuthStatus(status=ProviderAccountStatus.CONNECTED, detail=detail)
        return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="ANTHROPIC_API_KEY не задан")

    def supports_key_entry(self) -> bool:
        return True

    def update_api_key(self, api_key: str | None) -> None:
        self._api_key = api_key

    def set_extra_accounts(self, extra_accounts: list[str]) -> None:
        """Живое обновление списка доп. аккаунтов, без рестарта процесса —
        тот же принцип, что у update_api_key."""
        self._extra_accounts = list(extra_accounts)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        options = options or RunOptions()
        pairs = label_credentials(self._api_key, self._extra_accounts)
        return run_with_account_fallback(
            pairs,
            lambda label, api_key: self._run_once(api_key, prompt, options, account_label=label),
            not_configured_hint="ANTHROPIC_API_KEY не задан — залогинься в Настройках → 🔌 Провайдеры ИИ.",
            forced_account_label=options.forced_account_label,
        )

    def _run_once(
        self, api_key: str, prompt: str, options: RunOptions, *, account_label: str | None = None
    ) -> ProviderResult:
        import anthropic  # локальный импорт: не тянуть SDK, если провайдер не используется

        client = anthropic.Anthropic(api_key=api_key)
        try:
            message = client.messages.create(
                model=options.model or DEFAULT_MODEL,
                max_tokens=options.max_tokens,
                temperature=options.temperature,
                system=options.system or "",
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError as exc:
            # 429 — реальный сигнал квоты, отсюда движок пайплайна уходит в
            # HANDOVER (см. app.tasks.pipeline), а не просто падает с ошибкой.
            raise ProviderQuotaExceededError(f"Claude: превышен лимит запросов (429): {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 529:  # overloaded_error — ждать смысла столько же, сколько квоту
                raise ProviderQuotaExceededError(f"Claude перегружен (529): {exc}") from exc
            raise ProviderError(f"Claude API error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — оборачиваем любую другую ошибку SDK
            raise ProviderError(f"Claude API error: {exc}") from exc

        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
        self._quota_tracker.record(
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            account_label=account_label,
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
