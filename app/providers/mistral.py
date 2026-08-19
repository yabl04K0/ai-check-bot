"""Mistral — OpenAI-совместимый chat-эндпоинт (https://docs.mistral.ai/api)."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "mistral-large-latest"
BASE_URL = "https://api.mistral.ai/v1"


class MistralProvider(OpenAICompatibleProvider):
    name = ProviderName.MISTRAL
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Mistral"
