"""LAST_PROMPT.md — единственный слот "продолжи отсюда" между сессиями ИИ.

В отличие от chek_open/later/never.md (см. store.py) это не список записей, а
один свободный текст, который целиком перезаписывается новым (см.
AI_COMMANDS.md: PROMPT_WRITE/PROMPT_SHOW в ручном md-workflow). Источник
правды — файл в репозитории проекта, как и у реестра находок; отдельного
кэша в SQLite для него нет, т.к. используется только на чтение при сборе
intake (см. app.tasks.project_context.gather_last_prompt) и на явную
запись/просмотр из бота (см. app/bot/handlers/projects.py).
"""

from __future__ import annotations

from pathlib import Path

FILENAME = "LAST_PROMPT.md"


def read_last_prompt(project_path: Path) -> str:
    path = project_path / FILENAME
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_last_prompt(project_path: Path, text: str) -> None:
    path = project_path / FILENAME
    content = text.strip()
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
