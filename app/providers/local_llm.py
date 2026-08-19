"""Локальная LLM (Qwen2.5-Coder на RTX 3060) через Ollama/vLLM.

Без авторизации — просто OpenAI-совместимый endpoint. Роль scout+runner
в LITE ЧЕК (см. backend-architecture.mermaid, LITEFLOW).
"""

from __future__ import annotations

import httpx

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    ProviderError,
    ProviderResult,
    RunOptions,
)


class LocalLLMProvider(AIProvider):
    name = ProviderName.LOCAL_LLM

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def auth_status(self) -> AuthStatus:
        try:
            response = httpx.get(f"{self._base_url}/models", timeout=3)
            if response.status_code == 200:
                return AuthStatus(status=ProviderAccountStatus.CONNECTED, detail="online")
        except httpx.HTTPError:
            pass
        return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="endpoint offline")

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        options = options or RunOptions()
        messages = []
        if options.system:
            messages.append({"role": "system", "content": options.system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": options.model or self._model,
                    "messages": messages,
                    "max_tokens": options.max_tokens,
                    "temperature": options.temperature,
                },
                timeout=180,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Локальная LLM недоступна ({self._base_url}): {exc}") from exc

        data = response.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ProviderResult(
            text=choice,
            model=data.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )
