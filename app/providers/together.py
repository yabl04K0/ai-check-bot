"""Together AI — OpenAI-совместимый API (https://docs.together.ai/docs/openai-api-compatibility)."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
BASE_URL = "https://api.together.xyz/v1"


class TogetherProvider(OpenAICompatibleProvider):
    name = ProviderName.TOGETHER
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Together"
