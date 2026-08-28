from __future__ import annotations

from app.db.models import ProviderName
from app.providers import circuit_breaker
from app.providers.base import ProviderError, RunOptions

NO_ACTIVE_PROBE = frozenset({ProviderName.CLAUDE_CODE, ProviderName.CURSOR, ProviderName.CODEX})

PROBE_PROMPT = "ping"
PROBE_MAX_TOKENS = 4


def probe_account(registry, provider_name: ProviderName, account_label: str) -> bool:
    provider = registry.get(provider_name)
    options = RunOptions(max_tokens=PROBE_MAX_TOKENS, forced_account_label=account_label)
    try:
        provider.run_prompt(PROBE_PROMPT, options)
    except ProviderError:
        circuit_breaker.record_failure(provider_name, account_label)
        return False
    circuit_breaker.record_success(provider_name, account_label)
    return True
