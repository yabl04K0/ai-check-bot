"""Cerebras — OpenAI-совместимый API (https://inference-docs.cerebras.ai),
инференс на wafer-scale чипах — вместе с Groq второй сверхбыстрый scout-
кандидат для LITE ЧЕК."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "llama-3.3-70b"
BASE_URL = "https://api.cerebras.ai/v1"


class CerebrasProvider(OpenAICompatibleProvider):
    name = ProviderName.CEREBRAS
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Cerebras"
