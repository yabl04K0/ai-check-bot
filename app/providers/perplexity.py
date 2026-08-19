"""Perplexity (Sonar) — OpenAI-совместимый API (https://docs.perplexity.ai/api-reference).
Модели с встроенным web-поиском — полезны там, где нужен свежий контекст,
не только код в репо."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "sonar"
BASE_URL = "https://api.perplexity.ai"


class PerplexityProvider(OpenAICompatibleProvider):
    name = ProviderName.PERPLEXITY
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Perplexity"
