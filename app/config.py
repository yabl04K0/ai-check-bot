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
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen2.5-coder:14b"
    # Недельный бюджет токенов для оценки квоты автопроверки (см.
    # app.providers.quota) — официального API учёта нет, это ПРИБЛИЗИТЕЛЬНО.
    # Не задан → автопроверка по квоте для этого провайдера не сработает.
    anthropic_weekly_token_budget: int | None = None
    openai_weekly_token_budget: int | None = None


@dataclass(frozen=True)
class Settings:
    bot_token: str | None
    admin_tg_id: int | None
    providers: ProviderSettings
    github_token: str | None
    autocheck: AutocheckSettings
    db_path: Path
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)

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
            local_llm_base_url=os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"),
            local_llm_model=os.getenv("LOCAL_LLM_MODEL", "qwen2.5-coder:14b"),
            anthropic_weekly_token_budget=_int(os.getenv("ANTHROPIC_WEEKLY_TOKEN_BUDGET"), 0) or None,
            openai_weekly_token_budget=_int(os.getenv("OPENAI_WEEKLY_TOKEN_BUDGET"), 0) or None,
        ),
        github_token=os.getenv("GITHUB_TOKEN") or None,
        autocheck=AutocheckSettings(
            enabled=_bool(os.getenv("AUTOCHECK_ENABLED"), False),
            full_threshold_pct=_int(os.getenv("AUTOCHECK_FULL_THRESHOLD_PCT"), 60),
            lite_threshold_pct=_int(os.getenv("AUTOCHECK_LITE_THRESHOLD_PCT"), 90),
            lite_hours_before_reset=_int(os.getenv("AUTOCHECK_LITE_HOURS_BEFORE_RESET"), 1),
        ),
        db_path=project_root / "data" / "bot.sqlite3",
    )
