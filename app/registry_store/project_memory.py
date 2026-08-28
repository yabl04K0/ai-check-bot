"""PROJECT_MEMORY.md — durable архитектурная память + инженерный дневник
(SESSION LOG). Бот НИКОГДА не создаёт этот файл с нуля — это авторский
документ (архитектура, инварианты, история решений), а не то, что честно
можно сгенерировать из одного прогона job'ы (см. registry_store.state_log
для файла, который бот действительно вправе создавать сам). Бот только:
(1) читает секции ВЫШЕ SESSION LOG для контекста (см. CLAUDE.md/AI_COMMANDS.md
"ALWAYS read at session start — но секции выше SESSION LOG"), (2) дописывает
новую запись в конец SESSION LOG после прогона."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

FILENAME = "PROJECT_MEMORY.md"
SESSION_LOG_MARKER = "SESSION LOG"

_MSK = timezone(timedelta(hours=3))


def read_architecture(project_path: Path) -> str:
    """Всё содержимое файла ДО заголовка SESSION LOG (архитектура,
    структура, инварианты) — сам SESSION LOG не тянем, он для истории,
    не для контекста каждого прогона (см. докстринг модуля)."""
    path = project_path / FILENAME
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    marker_index = text.find(SESSION_LOG_MARKER)
    return text if marker_index == -1 else text[:marker_index].rstrip()


def append_session_log_entry(project_path: Path, title: str, body: str) -> bool:
    """Возвращает False, если PROJECT_MEMORY.md ещё не существует — бот
    не заводит его сам (см. докстринг модуля), только дописывает в уже
    существующий, авторский файл."""
    path = project_path / FILENAME
    if not path.exists():
        return False
    now = datetime.now(_MSK)
    header = f"--- {now:%Y-%m-%d %H:%M} МСК - {title} ---"
    entry = f"\n{header}\n{body.strip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    return True
