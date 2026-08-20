"""Environment-backed settings. No provider SDK calls here — see providers/."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(os.getenv("AI_CHECK_BOT_DB_PATH", "ai_check_bot.db"))

MAX_PROBES_PER_DAY = 5


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    admin_tg_id: int | None = field(
        default_factory=lambda: int(os.getenv("ADMIN_TG_ID")) if os.getenv("ADMIN_TG_ID") else None
    )
    db_path: Path = DB_PATH


def get_settings() -> Settings:
    return Settings()
