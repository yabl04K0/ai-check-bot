"""Единая точка сборки провайдеров из конфига."""

from __future__ import annotations

from app.config import Settings
from app.db.models import ProviderName
from app.providers.base import AIProvider
from app.providers.claude import ClaudeProvider
from app.providers.codex import CodexProvider
from app.providers.cursor import CursorProvider
from app.providers.local_llm import LocalLLMProvider
from app.providers.quota import QuotaTracker


def build_providers(settings: Settings) -> dict[ProviderName, AIProvider]:
    p = settings.providers
    return {
        ProviderName.CLAUDE: ClaudeProvider(
            p.anthropic_api_key, QuotaTracker(ProviderName.CLAUDE, p.anthropic_weekly_token_budget)
        ),
        ProviderName.CODEX: CodexProvider(
            p.openai_api_key, QuotaTracker(ProviderName.CODEX, p.openai_weekly_token_budget)
        ),
        ProviderName.CURSOR: CursorProvider(p.cursor_agent_cli_path),
        ProviderName.LOCAL_LLM: LocalLLMProvider(p.local_llm_base_url, p.local_llm_model),
    }


class ProviderRegistry:
    """Хранит собранные провайдеры, отдаёт по имени."""

    def __init__(self, providers: dict[ProviderName, AIProvider]) -> None:
        self._providers = providers

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderRegistry":
        return cls(build_providers(settings))

    def get(self, name: ProviderName) -> AIProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"Неизвестный провайдер: {name}") from exc

    def all(self) -> dict[ProviderName, AIProvider]:
        return dict(self._providers)

    def connected(self) -> list[ProviderName]:
        from app.db.models import ProviderAccountStatus

        return [
            name
            for name, provider in self._providers.items()
            if provider.auth_status().status == ProviderAccountStatus.CONNECTED
        ]
