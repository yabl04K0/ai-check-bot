"""Какие (provider, account_label) реально нуждаются в прокси — только
провайдеры, которые реально используют назначенный прокси: девять на
app.providers.openai_compatible.OpenAICompatibleProvider (httpx proxy=,
см. resolve_proxy_url_safe в _run_once) плюс claude_code (subprocess,
HTTP_PROXY/HTTPS_PROXY в окружении процесса, см.
app.providers.claude_code_cli._run_once). Остальные (Claude API, Codex,
локальная LLM, Cursor) пока не участвуют — не заводить им "мёртвые"
назначения, которые никто не использует."""

from __future__ import annotations

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.accounts_store import list_extra_accounts
from app.providers.registry import ProviderRegistry
from app.proxies.pool import Consumer

PROXIED_PROVIDERS = frozenset(
    {
        ProviderName.GEMINI,
        ProviderName.DEEPSEEK,
        ProviderName.GROK,
        ProviderName.GROQ,
        ProviderName.MISTRAL,
        ProviderName.OPENROUTER,
        ProviderName.TOGETHER,
        ProviderName.PERPLEXITY,
        ProviderName.FIREWORKS,
        ProviderName.CEREBRAS,
        ProviderName.CLAUDE_CODE,
    }
)


def active_consumers(registry: ProviderRegistry) -> list[Consumer]:
    """Каждый реально подключённый основной ключ ("primary") плюс каждый
    добавленный доп. аккаунт ("extra:N") среди проксируемых провайдеров."""
    consumers: list[Consumer] = []
    for name, provider in registry.all().items():
        if name not in PROXIED_PROVIDERS:
            continue
        if provider.auth_status().status == ProviderAccountStatus.CONNECTED:
            consumers.append(Consumer(provider=name, account_label="primary"))
        for i, _account in enumerate(list_extra_accounts(name), start=1):
            consumers.append(Consumer(provider=name, account_label=f"extra:{i}"))
    return consumers
