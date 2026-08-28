"""Модель по умолчанию для OpenAI-совместимых провайдеров (Gemini/DeepSeek/
Grok/Groq/Mistral/OpenRouter/Together/Perplexity/Fireworks) — можно сменить
прямо из бота (⚙️ Настройки → 🔌 Провайдеры ИИ → 🔑 Ключ → 🧠 Модель), в
дополнение к статичным `*_MODEL` в `.env`. Тот же паттерн key/value в
BotSetting, что и у app.providers.key_store (API-ключи): значение из бота
важнее .env, .env — фолбэк, "Сбросить" явно возвращает .env-значение."""

from __future__ import annotations

from app.config import ProviderSettings
from app.db.models import BotSetting, ProviderName
from app.db.session import get_session

_SETTING_PREFIX = "provider_model_override"

_ENV_ATTR: dict[ProviderName, str] = {
    ProviderName.GEMINI: "gemini_model",
    ProviderName.DEEPSEEK: "deepseek_model",
    ProviderName.GROK: "grok_model",
    ProviderName.GROQ: "groq_model",
    ProviderName.MISTRAL: "mistral_model",
    ProviderName.OPENROUTER: "openrouter_model",
    ProviderName.TOGETHER: "together_model",
    ProviderName.PERPLEXITY: "perplexity_model",
    ProviderName.FIREWORKS: "fireworks_model",
}


def supports_model_override(provider: ProviderName) -> bool:
    return provider in _ENV_ATTR


def _setting_key(provider: ProviderName) -> str:
    return f"{_SETTING_PREFIX}:{provider.value}"


def get_model_override(provider: ProviderName) -> str | None:
    with get_session() as session:
        row = session.get(BotSetting, _setting_key(provider))
        return row.value if row and row.value else None


def set_model_override(provider: ProviderName, model: str) -> None:
    setting_key = _setting_key(provider)
    with get_session() as session:
        row = session.get(BotSetting, setting_key)
        if row is None:
            session.add(BotSetting(key=setting_key, value=model))
        else:
            row.value = model


def clear_model_override(provider: ProviderName) -> None:
    setting_key = _setting_key(provider)
    with get_session() as session:
        row = session.get(BotSetting, setting_key)
        if row is not None:
            session.delete(row)


def env_default_model(provider: ProviderName, providers: ProviderSettings) -> str | None:
    attr = _ENV_ATTR.get(provider)
    return getattr(providers, attr) if attr else None


def resolve_model(provider: ProviderName, providers: ProviderSettings) -> str | None:
    """None означает "используй _default_model самого провайдера" (см.
    OpenAICompatibleProvider.__init__: model or self._default_model)."""
    return get_model_override(provider) or env_default_model(provider, providers)
