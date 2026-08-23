"""Единая точка сборки провайдеров из конфига."""

from __future__ import annotations

from app.config import Settings
from app.db.models import ProviderName
from app.github_integration.token_store import resolve_github_token
from app.providers.base import AIProvider
from app.providers.cerebras import CerebrasProvider
from app.providers.claude import ClaudeProvider
from app.providers.codex import CodexProvider
from app.providers.cursor import CursorProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.fireworks import FireworksProvider
from app.providers.gemini import GeminiProvider
from app.providers.grok import GrokProvider
from app.providers.groq import GroqProvider
from app.providers.local_llm import LocalLLMProvider
from app.providers.mistral import MistralProvider
from app.providers.openrouter import OpenRouterProvider
from app.providers.perplexity import PerplexityProvider
from app.providers.quota import QuotaTracker
from app.providers.together import TogetherProvider


def build_providers(settings: Settings) -> dict[ProviderName, AIProvider]:
    p = settings.providers
    return {
        ProviderName.CLAUDE: ClaudeProvider(
            p.anthropic_api_key, QuotaTracker(ProviderName.CLAUDE, p.anthropic_weekly_token_budget)
        ),
        ProviderName.CODEX: CodexProvider(
            p.openai_api_key,
            QuotaTracker(ProviderName.CODEX, p.openai_weekly_token_budget),
            cli_path=p.codex_cli_path,
        ),
        ProviderName.CURSOR: CursorProvider(
            p.cursor_agent_cli_path, github_token=resolve_github_token(settings)
        ),
        ProviderName.LOCAL_LLM: LocalLLMProvider(p.local_llm_base_url, p.local_llm_model),
        ProviderName.GEMINI: GeminiProvider(
            p.gemini_api_key,
            QuotaTracker(ProviderName.GEMINI, p.gemini_weekly_token_budget),
            model=p.gemini_model,
        ),
        ProviderName.DEEPSEEK: DeepSeekProvider(
            p.deepseek_api_key,
            QuotaTracker(ProviderName.DEEPSEEK, p.deepseek_weekly_token_budget),
            model=p.deepseek_model,
        ),
        ProviderName.GROK: GrokProvider(
            p.grok_api_key,
            QuotaTracker(ProviderName.GROK, p.grok_weekly_token_budget),
            model=p.grok_model,
        ),
        ProviderName.GROQ: GroqProvider(
            p.groq_api_key,
            QuotaTracker(ProviderName.GROQ, p.groq_weekly_token_budget),
            model=p.groq_model,
        ),
        ProviderName.MISTRAL: MistralProvider(
            p.mistral_api_key,
            QuotaTracker(ProviderName.MISTRAL, p.mistral_weekly_token_budget),
            model=p.mistral_model,
        ),
        ProviderName.OPENROUTER: OpenRouterProvider(
            p.openrouter_api_key,
            QuotaTracker(ProviderName.OPENROUTER, p.openrouter_weekly_token_budget),
            model=p.openrouter_model,
        ),
        ProviderName.TOGETHER: TogetherProvider(
            p.together_api_key,
            QuotaTracker(ProviderName.TOGETHER, p.together_weekly_token_budget),
            model=p.together_model,
        ),
        ProviderName.PERPLEXITY: PerplexityProvider(
            p.perplexity_api_key,
            QuotaTracker(ProviderName.PERPLEXITY, p.perplexity_weekly_token_budget),
            model=p.perplexity_model,
        ),
        ProviderName.FIREWORKS: FireworksProvider(
            p.fireworks_api_key,
            QuotaTracker(ProviderName.FIREWORKS, p.fireworks_weekly_token_budget),
            model=p.fireworks_model,
        ),
        ProviderName.CEREBRAS: CerebrasProvider(
            p.cerebras_api_key,
            QuotaTracker(ProviderName.CEREBRAS, p.cerebras_weekly_token_budget),
            model=p.cerebras_model,
        ),
    }


class ProviderRegistry:
    """Хранит собранные провайдеры, отдаёт по имени.

    disable()/enable() — "мягкое" отключение из бота (⚙️ Настройки →
    🔌 Провайдеры ИИ → Отключить): секрет в .env не трогаем и не можем
    (это не сессия, а статический ключ/CLI-путь), но роутер и connected()
    перестают его видеть, пока не нажали "Подключить обратно"."""

    def __init__(self, providers: dict[ProviderName, AIProvider]) -> None:
        self._providers = providers
        self._disabled: set[ProviderName] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderRegistry:
        return cls(build_providers(settings))

    def get(self, name: ProviderName) -> AIProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"Неизвестный провайдер: {name}") from exc

    def all(self) -> dict[ProviderName, AIProvider]:
        return dict(self._providers)

    def disable(self, name: ProviderName) -> None:
        self._disabled.add(name)

    def enable(self, name: ProviderName) -> None:
        self._disabled.discard(name)

    def is_disabled(self, name: ProviderName) -> bool:
        return name in self._disabled

    def connected(self) -> list[ProviderName]:
        from app.db.models import ProviderAccountStatus

        return [
            name
            for name, provider in self._providers.items()
            if name not in self._disabled
            and provider.auth_status().status == ProviderAccountStatus.CONNECTED
        ]
