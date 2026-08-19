"""Groq — OpenAI-совместимый API (https://console.groq.com/docs/openai),
LPU-инференс с очень низкой задержкой — удобен как быстрый scout/Lite-режим
наравне с локальной LLM."""

from __future__ import annotations

from app.db.models import ProviderName
from app.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_MODEL = "llama-3.3-70b-versatile"
BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(OpenAICompatibleProvider):
    name = ProviderName.GROQ
    _base_url = BASE_URL
    _default_model = DEFAULT_MODEL
    _display_name = "Groq"
