"""Стандартный logging + append-only лог действий бота (STATE_LOG-стиль,
см. README "История и админка" → 🪵 Логи бота)."""

from __future__ import annotations

import logging

from app.db.models import ActionLog
from app.db.session import get_session


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def log_action(actor: str, action: str, details: str | None = None) -> None:
    """Append-only запись в action_log. Никогда не должна редактироваться
    или удаляться постфактум — это аудит-лог действий бота."""
    with get_session() as session:
        session.add(ActionLog(actor=actor, action=action, details=details))
