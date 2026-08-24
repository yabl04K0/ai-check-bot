"""API-ключи AI-провайдеров, которые можно задать/обновить прямо из бота
(⚙️ Настройки → 🔌 Провайдеры ИИ → 🔑 Ключ), в дополнение к статичным
`*_API_KEY` в `.env` — тот же паттерн, что и у GitHub-токена, см.
`app.github_integration.token_store`.

Хранится в `BotSetting` (тот же key/value стор, что и тумблеры автономности
ИИ и GitHub-токен) — переживает рестарт бота. Ключ, заданный через бота,
имеет приоритет над `.env`, а не наоборот; чтобы вернуться к значению из
`.env`, нужно явно нажать «Убрать» в меню ключа."""

from __future__ import annotations

from app.config import ProviderSettings
from app.db.models import BotSetting, ProviderName
from app.db.session import get_session

_SETTING_PREFIX = "provider_api_key_override"

# Провайдеры на чистом API-ключе/токене (см. app.config.ProviderSettings) —
# CURSOR (CLI-логин) и LOCAL_LLM (без авторизации) сюда намеренно не входят.
_ENV_ATTR: dict[ProviderName, str] = {
    ProviderName.CLAUDE: "anthropic_api_key",
    # CLAUDE_CODE — не API-ключ, а CLAUDE_CODE_OAUTH_TOKEN основного слота
    # (см. app.providers.claude_code_cli) — но тот же key_store/UI-паттерн
    # ("🔑 Ключ") подходит один в один: секрет-строка, перекрывающая .env.
    ProviderName.CLAUDE_CODE: "claude_code_oauth_token",
    ProviderName.CODEX: "openai_api_key",
    ProviderName.GEMINI: "gemini_api_key",
    ProviderName.DEEPSEEK: "deepseek_api_key",
    ProviderName.GROK: "grok_api_key",
    ProviderName.GROQ: "groq_api_key",
    ProviderName.MISTRAL: "mistral_api_key",
    ProviderName.OPENROUTER: "openrouter_api_key",
    ProviderName.TOGETHER: "together_api_key",
    ProviderName.PERPLEXITY: "perplexity_api_key",
    ProviderName.FIREWORKS: "fireworks_api_key",
    ProviderName.CEREBRAS: "cerebras_api_key",
}


def _setting_key(provider: ProviderName) -> str:
    return f"{_SETTING_PREFIX}:{provider.value}"


def get_key_override(provider: ProviderName) -> str | None:
    with get_session() as session:
        row = session.get(BotSetting, _setting_key(provider))
        return row.value if row and row.value else None


def set_key_override(provider: ProviderName, api_key: str) -> None:
    setting_key = _setting_key(provider)
    with get_session() as session:
        row = session.get(BotSetting, setting_key)
        if row is None:
            session.add(BotSetting(key=setting_key, value=api_key))
        else:
            row.value = api_key


def clear_key_override(provider: ProviderName) -> None:
    setting_key = _setting_key(provider)
    with get_session() as session:
        row = session.get(BotSetting, setting_key)
        if row is not None:
            session.delete(row)


def env_default_key(provider: ProviderName, providers: ProviderSettings) -> str | None:
    """Значение из `.env` для этого провайдера — для CURSOR/LOCAL_LLM всегда
    None (у них нет отдельного API-ключа в ProviderSettings)."""
    attr = _ENV_ATTR.get(provider)
    return getattr(providers, attr) if attr else None


def resolve_api_key(provider: ProviderName, providers: ProviderSettings) -> str | None:
    """Ключ из бота (если задан) имеет приоритет над `.env` — см. докстринг модуля."""
    return get_key_override(provider) or env_default_key(provider, providers)
