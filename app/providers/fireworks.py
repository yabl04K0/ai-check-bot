"""Fireworks AI — OpenAI-совместимый API (https://docs.fireworks.ai/api-reference/post-chatcompletions)."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "accounts/fireworks/models/llama-v3p3-70b-instruct"
BASE_URL = "https://api.fireworks.ai/inference/v1"


class FireworksProvider(OpenAICompatibleProvider):
    name = ProviderName.FIREWORKS
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Fireworks"
