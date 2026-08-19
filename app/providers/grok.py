"""Grok (xAI) — OpenAI-совместимый API (https://docs.x.ai/docs/api-reference)."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "grok-4"
BASE_URL = "https://api.x.ai/v1"


class GrokProvider(OpenAICompatibleProvider):
    name = ProviderName.GROK
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Grok"
