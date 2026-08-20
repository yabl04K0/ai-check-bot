"""Maps AIAccount.provider strings to AIProvider subclasses. Add a new provider here and
nowhere else — no call site should import a provider class directly."""
from __future__ import annotations

from ai_check_bot.models import AIAccount
from ai_check_bot.providers.base import AIProvider
from ai_check_bot.providers.claude import ClaudeProvider

PROVIDER_REGISTRY: dict[str, type[AIProvider]] = {
    "claude": ClaudeProvider,
}


def get_provider(account: AIAccount) -> AIProvider:
    try:
        provider_cls = PROVIDER_REGISTRY[account.provider]
    except KeyError:
        raise ValueError(
            f"unknown provider '{account.provider}', known: {sorted(PROVIDER_REGISTRY)}"
        ) from None
    return provider_cls(api_key=account.api_key, proxy_url=account.proxy_url)
