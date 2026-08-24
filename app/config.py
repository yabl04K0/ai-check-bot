"""Загрузка конфигурации из окружения (.env).

Источник правды по набору переменных — .env.example в корне репо.
Все значения читаются один раз при старте и складываются в неизменяемый
объект Settings, который прокидывается по коду явно (без глобального
импорта переменных окружения где попало).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class AutocheckSettings:
    enabled: bool = False
    full_threshold_pct: int = 60
    lite_threshold_pct: int = 90
    lite_hours_before_reset: int = 1


@dataclass(frozen=True)
class ProviderSettings:
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    cursor_agent_cli_path: str | None = None
    codex_cli_path: str | None = None

    # Claude Code CLI (см. app.providers.claude_code_cli) — исполнение через
    # `claude -p` на подписке Max/Pro, не метрируемый ANTHROPIC_API_KEY выше.
    # Без claude_code_oauth_token основной слот берёт обычную сессию `claude`
    # на этой машине; дополнительные аккаунты (произвольно много, см. "➕
    # Добавить ещё аккаунт" в боте) всегда требуют CLAUDE_CODE_OAUTH_TOKEN
    # (см. `claude setup-token`, выполняется вручную в терминале).
    claude_cli_path: str | None = None
    claude_code_oauth_token: str | None = None
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen2.5-coder:14b"
    # Недельный бюджет токенов для оценки квоты автопроверки (см.
    # app.providers.quota) — официального API учёта нет, это ПРИБЛИЗИТЕЛЬНО.
    # Не задан → автопроверка по квоте для этого провайдера не сработает.
    anthropic_weekly_token_budget: int | None = None
    openai_weekly_token_budget: int | None = None

    # Доп. провайдеры через общий OpenAI-совместимый контракт (см.
    # app.providers.openai_compatible) — каждый заводится тут одинаково:
    # API-ключ, опциональный override модели, опциональный недельный бюджет.
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_weekly_token_budget: int | None = None

    deepseek_api_key: str | None = None
    deepseek_model: str | None = None
    deepseek_weekly_token_budget: int | None = None

    grok_api_key: str | None = None
    grok_model: str | None = None
    grok_weekly_token_budget: int | None = None

    groq_api_key: str | None = None
    groq_model: str | None = None
    groq_weekly_token_budget: int | None = None

    mistral_api_key: str | None = None
    mistral_model: str | None = None
    mistral_weekly_token_budget: int | None = None

    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    openrouter_weekly_token_budget: int | None = None

    together_api_key: str | None = None
    together_model: str | None = None
    together_weekly_token_budget: int | None = None

    perplexity_api_key: str | None = None
    perplexity_model: str | None = None
    perplexity_weekly_token_budget: int | None = None

    fireworks_api_key: str | None = None
    fireworks_model: str | None = None
    fireworks_weekly_token_budget: int | None = None

    cerebras_api_key: str | None = None
    cerebras_model: str | None = None
    cerebras_weekly_token_budget: int | None = None


@dataclass(frozen=True)
class NotificationSettings:
    """Опциональное дублирование отчёта о задаче во внешние каналы (см.
    app.notifications.webhook) — Telegram остаётся основным и
    единственным обязательным."""

    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None


@dataclass(frozen=True)
class Settings:
    bot_token: str | None
    admin_tg_id: int | None
    providers: ProviderSettings
    github_token: str | None
    autocheck: AutocheckSettings
    db_path: Path
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    # Директория с локальными чекаутами (см. app.tasks.local_repos) — для
    # удобного выбора репо кнопкой при добавлении проекта. Не задано =
    # добавление проекта работает как раньше, только ручной ввод.
    local_repos_root: Path | None = None
    notifications: NotificationSettings = field(default_factory=NotificationSettings)

    def require_bot_token(self) -> str:
        if not self.bot_token:
            raise RuntimeError(
                "BOT_TOKEN не задан. Заполни .env (см. .env.example)."
            )
        return self.bot_token


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Читает .env (если есть) и переменные окружения в объект Settings."""
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)

    project_root = Path(__file__).resolve().parent.parent

    admin_id_raw = os.getenv("ADMIN_TG_ID")
    admin_tg_id = int(admin_id_raw) if admin_id_raw and admin_id_raw.strip().isdigit() else None

    return Settings(
        bot_token=os.getenv("BOT_TOKEN") or None,
        admin_tg_id=admin_tg_id,
        providers=ProviderSettings(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            cursor_agent_cli_path=os.getenv("CURSOR_AGENT_CLI_PATH") or None,
            codex_cli_path=os.getenv("CODEX_CLI_PATH") or None,
            claude_cli_path=os.getenv("CLAUDE_CLI_PATH") or None,
            claude_code_oauth_token=os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or None,
            local_llm_base_url=os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"),
            local_llm_model=os.getenv("LOCAL_LLM_MODEL", "qwen2.5-coder:14b"),
            anthropic_weekly_token_budget=_int(os.getenv("ANTHROPIC_WEEKLY_TOKEN_BUDGET"), 0) or None,
            openai_weekly_token_budget=_int(os.getenv("OPENAI_WEEKLY_TOKEN_BUDGET"), 0) or None,
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            gemini_model=os.getenv("GEMINI_MODEL") or None,
            gemini_weekly_token_budget=_int(os.getenv("GEMINI_WEEKLY_TOKEN_BUDGET"), 0) or None,
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_model=os.getenv("DEEPSEEK_MODEL") or None,
            deepseek_weekly_token_budget=_int(os.getenv("DEEPSEEK_WEEKLY_TOKEN_BUDGET"), 0) or None,
            grok_api_key=os.getenv("GROK_API_KEY") or None,
            grok_model=os.getenv("GROK_MODEL") or None,
            grok_weekly_token_budget=_int(os.getenv("GROK_WEEKLY_TOKEN_BUDGET"), 0) or None,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            groq_model=os.getenv("GROQ_MODEL") or None,
            groq_weekly_token_budget=_int(os.getenv("GROQ_WEEKLY_TOKEN_BUDGET"), 0) or None,
            mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
            mistral_model=os.getenv("MISTRAL_MODEL") or None,
            mistral_weekly_token_budget=_int(os.getenv("MISTRAL_WEEKLY_TOKEN_BUDGET"), 0) or None,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv("OPENROUTER_MODEL") or None,
            openrouter_weekly_token_budget=_int(os.getenv("OPENROUTER_WEEKLY_TOKEN_BUDGET"), 0) or None,
            together_api_key=os.getenv("TOGETHER_API_KEY") or None,
            together_model=os.getenv("TOGETHER_MODEL") or None,
            together_weekly_token_budget=_int(os.getenv("TOGETHER_WEEKLY_TOKEN_BUDGET"), 0) or None,
            perplexity_api_key=os.getenv("PERPLEXITY_API_KEY") or None,
            perplexity_model=os.getenv("PERPLEXITY_MODEL") or None,
            perplexity_weekly_token_budget=_int(os.getenv("PERPLEXITY_WEEKLY_TOKEN_BUDGET"), 0) or None,
            fireworks_api_key=os.getenv("FIREWORKS_API_KEY") or None,
            fireworks_model=os.getenv("FIREWORKS_MODEL") or None,
            fireworks_weekly_token_budget=_int(os.getenv("FIREWORKS_WEEKLY_TOKEN_BUDGET"), 0) or None,
            cerebras_api_key=os.getenv("CEREBRAS_API_KEY") or None,
            cerebras_model=os.getenv("CEREBRAS_MODEL") or None,
            cerebras_weekly_token_budget=_int(os.getenv("CEREBRAS_WEEKLY_TOKEN_BUDGET"), 0) or None,
        ),
        github_token=os.getenv("GITHUB_TOKEN") or None,
        autocheck=AutocheckSettings(
            enabled=_bool(os.getenv("AUTOCHECK_ENABLED"), False),
            full_threshold_pct=_int(os.getenv("AUTOCHECK_FULL_THRESHOLD_PCT"), 60),
            lite_threshold_pct=_int(os.getenv("AUTOCHECK_LITE_THRESHOLD_PCT"), 90),
            lite_hours_before_reset=_int(os.getenv("AUTOCHECK_LITE_HOURS_BEFORE_RESET"), 1),
        ),
        db_path=project_root / "data" / "bot.sqlite3",
        local_repos_root=Path(os.getenv("LOCAL_REPOS_ROOT")) if os.getenv("LOCAL_REPOS_ROOT") else None,
        notifications=NotificationSettings(
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
        ),
    )
