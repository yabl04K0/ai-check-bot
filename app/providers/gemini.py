"""Google Gemini через официальный OpenAI-совместимый endpoint
(https://ai.google.dev/gemini-api/docs/openai) — не нужен отдельный SDK,
тот же Chat Completions контракт, что и у остальных провайдеров этого
семейства (см. app.providers.openai_compatible)."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "gemini-2.5-flash"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiProvider(OpenAICompatibleProvider):
    name = ProviderName.GEMINI
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Gemini"
