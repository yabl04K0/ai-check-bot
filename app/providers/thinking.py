from __future__ import annotations

from app.db.models import BotSetting
from app.db.session import get_session

_KEY = "thinking_level"

LEVELS = ("off", "low", "medium", "high")

_INSTRUCTION = {
    "low": "Прежде чем ответить, немного обдумай задачу (think).",
    "medium": "Прежде чем ответить, тщательно обдумай задачу шаг за шагом (think hard).",
    "high": (
        "Прежде чем ответить, обдумай задачу максимально глубоко: рассмотри "
        "альтернативы, риски и краевые случаи (ultrathink)."
    ),
}


def thinking_level() -> str:
    with get_session() as session:
        row = session.get(BotSetting, _KEY)
        return row.value if row and row.value in LEVELS else "off"


def set_thinking_level(level: str) -> None:
    if level not in LEVELS:
        raise ValueError(f"Неизвестный уровень мышления: {level} (доступно: {', '.join(LEVELS)})")
    with get_session() as session:
        row = session.get(BotSetting, _KEY)
        if row is None:
            session.add(BotSetting(key=_KEY, value=level))
        else:
            row.value = level


def thinking_instruction(level: str | None = None) -> str | None:
    level = level if level is not None else thinking_level()
    return _INSTRUCTION.get(level)
