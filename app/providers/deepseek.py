"""DeepSeek — OpenAI-совместимый API (https://api-docs.deepseek.com)."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAICompatibleProvider):
    name = ProviderName.DEEPSEEK
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "DeepSeek"
