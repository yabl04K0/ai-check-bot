"""Единая точка сборки провайдеров из конфига."""

from __future__ import annotations

from app.config import Settings
from app.db.models import ProviderName
from app.github_integration.token_store import resolve_github_token
from app.providers.accounts_store import list_extra_secrets
from app.providers.base import AIProvider
from app.providers.cerebras import CerebrasProvider
from app.providers.claude import ClaudeProvider
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.providers.codex import CodexProvider
from app.providers.cursor import CursorProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.fireworks import FireworksProvider
from app.providers.gemini import GeminiProvider
from app.providers.grok import GrokProvider
from app.providers.groq import GroqProvider
from app.providers.key_store import resolve_api_key
from app.providers.local_llm import LocalLLMProvider
from app.providers.mistral import MistralProvider
from app.providers.openrouter import OpenRouterProvider
from app.providers.perplexity import PerplexityProvider
from app.providers.quota import QuotaTracker
from app.providers.together import TogetherProvider


def build_providers(settings: Settings) -> dict[ProviderName, AIProvider]:
    p = settings.providers
    extras = list_extra_secrets  # короткий алиас — "➕ Добавить ещё аккаунт", см. accounts_store
    return {
        ProviderName.CLAUDE: ClaudeProvider(
            resolve_api_key(ProviderName.CLAUDE, p),
            QuotaTracker(ProviderName.CLAUDE, p.anthropic_weekly_token_budget),
            extra_accounts=extras(ProviderName.CLAUDE),
        ),
        ProviderName.CLAUDE_CODE: ClaudeCodeCliProvider(
            p.claude_cli_path,
            resolve_api_key(ProviderName.CLAUDE_CODE, p),
            extra_accounts=extras(ProviderName.CLAUDE_CODE),
        ),
        ProviderName.CODEX: CodexProvider(
            resolve_api_key(ProviderName.CODEX, p),
            QuotaTracker(ProviderName.CODEX, p.openai_weekly_token_budget),
            cli_path=p.codex_cli_path,
            extra_accounts=extras(ProviderName.CODEX),
        ),
        ProviderName.CURSOR: CursorProvider(
            p.cursor_agent_cli_path, github_token=resolve_github_token(settings)
        ),
        ProviderName.LOCAL_LLM: LocalLLMProvider(p.local_llm_base_url, p.local_llm_model),
        ProviderName.GEMINI: GeminiProvider(
            resolve_api_key(ProviderName.GEMINI, p),
            QuotaTracker(ProviderName.GEMINI, p.gemini_weekly_token_budget),
            model=p.gemini_model,
            extra_accounts=extras(ProviderName.GEMINI),
        ),
        ProviderName.DEEPSEEK: DeepSeekProvider(
            resolve_api_key(ProviderName.DEEPSEEK, p),
            QuotaTracker(ProviderName.DEEPSEEK, p.deepseek_weekly_token_budget),
            model=p.deepseek_model,
            extra_accounts=extras(ProviderName.DEEPSEEK),
        ),
        ProviderName.GROK: GrokProvider(
            resolve_api_key(ProviderName.GROK, p),
            QuotaTracker(ProviderName.GROK, p.grok_weekly_token_budget),
            model=p.grok_model,
            extra_accounts=extras(ProviderName.GROK),
        ),
        ProviderName.GROQ: GroqProvider(
            resolve_api_key(ProviderName.GROQ, p),
            QuotaTracker(ProviderName.GROQ, p.groq_weekly_token_budget),
            model=p.groq_model,
            extra_accounts=extras(ProviderName.GROQ),
        ),
        ProviderName.MISTRAL: MistralProvider(
            resolve_api_key(ProviderName.MISTRAL, p),
            QuotaTracker(ProviderName.MISTRAL, p.mistral_weekly_token_budget),
            model=p.mistral_model,
            extra_accounts=extras(ProviderName.MISTRAL),
        ),
        ProviderName.OPENROUTER: OpenRouterProvider(
            resolve_api_key(ProviderName.OPENROUTER, p),
            QuotaTracker(ProviderName.OPENROUTER, p.openrouter_weekly_token_budget),
            model=p.openrouter_model,
            extra_accounts=extras(ProviderName.OPENROUTER),
        ),
        ProviderName.TOGETHER: TogetherProvider(
            resolve_api_key(ProviderName.TOGETHER, p),
            QuotaTracker(ProviderName.TOGETHER, p.together_weekly_token_budget),
            model=p.together_model,
            extra_accounts=extras(ProviderName.TOGETHER),
        ),
        ProviderName.PERPLEXITY: PerplexityProvider(
            resolve_api_key(ProviderName.PERPLEXITY, p),
            QuotaTracker(ProviderName.PERPLEXITY, p.perplexity_weekly_token_budget),
            model=p.perplexity_model,
            extra_accounts=extras(ProviderName.PERPLEXITY),
        ),
        ProviderName.FIREWORKS: FireworksProvider(
            resolve_api_key(ProviderName.FIREWORKS, p),
            QuotaTracker(ProviderName.FIREWORKS, p.fireworks_weekly_token_budget),
            model=p.fireworks_model,
            extra_accounts=extras(ProviderName.FIREWORKS),
        ),
        ProviderName.CEREBRAS: CerebrasProvider(
            resolve_api_key(ProviderName.CEREBRAS, p),
            QuotaTracker(ProviderName.CEREBRAS, p.cerebras_weekly_token_budget),
            model=p.cerebras_model,
            extra_accounts=extras(ProviderName.CEREBRAS),
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
