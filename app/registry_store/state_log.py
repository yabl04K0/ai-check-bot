"""STATE_LOG.md — append-only машинный лог событий проекта (LLM-only,
английский, формат из самого STATE_LOG.md/CLAUDE.md проекта: заголовок
`--- [PREFIX] YYYY-MM-DD HH:MM МСК (HH:MM UTC) ---`, дальше `key: value`).

Бот только дописывает записи в конец в этом же формате — не парсит и не
переписывает старые (лог истории, см. правило APPEND в самом файле), чтобы
ручные Claude Code сессии и бот делили одну и ту же историю. Файл
создаётся ботом при первом обращении, если его ещё нет (в отличие от
PROJECT_MEMORY.md — см. registry_store.project_memory)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

FILENAME = "STATE_LOG.md"

_MSK = timezone(timedelta(hours=3))

_HEADER = (
    "# STATE_LOG — append-only machine log of runtime state (LLM-ONLY, English on purpose)\n\n"
    "FORMAT: LLM only. Flat text, `key: value`, facts. NOT for humans — no decoration, no tables, no prose.\n"
    "APPEND: new entries go AT THE END. Never rewrite an old entry (the log is history).\n"
    "ENTRY: each entry starts with `--- [PREFIX] YYYY-MM-DD HH:MM МСК (HH:MM UTC) ---`,\n"
    "then `key: value` lines.\n\n"
    "# === entries below (append) ===\n"
)


def _format_entry(prefix: str, fields: dict[str, str]) -> str:
    now_msk = datetime.now(_MSK)
    now_utc = datetime.now(timezone.utc)
    header = f"--- [{prefix}] {now_msk:%Y-%m-%d %H:%M} МСК ({now_utc:%H:%M} UTC) ---"
    lines = [header] + [f"{key}: {value}" for key, value in fields.items()]
    return "\n".join(lines)


def append_entry(project_path: Path, prefix: str, fields: dict[str, str]) -> None:
    path = project_path / FILENAME
    entry = _format_entry(prefix, fields)
    if not path.exists():
        path.write_text(_HEADER + "\n" + entry + "\n\n", encoding="utf-8")
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(entry + "\n\n")


def read_tail(project_path: Path, *, max_lines: int = 200) -> str:
    """Только хвост — см. правило самого файла "NEVER read it whole"."""
    path = project_path / FILENAME
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-max_lines:])
