"""OpenRouter — единый OpenAI-совместимый шлюз к десяткам моделей разных
вендоров (https://openrouter.ai/docs) одним ключом. Модель по умолчанию —
"openrouter/auto", сам OpenRouter выбирает подходящую под запрос; можно
переопределить через OPENROUTER_MODEL в .env."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "openrouter/auto"
BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAICompatibleProvider):
    name = ProviderName.OPENROUTER
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "OpenRouter"
