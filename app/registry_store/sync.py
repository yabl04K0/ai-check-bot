"""Синк chek_open/later/never.md → таблица findings (SQLite-кэш для UI).

Источник правды остаётся в .md-файлах (см. store.py) — эта функция только
приводит кэш в соответствие с ними. Вызывается после любого действия,
которое могло изменить .md-файлы проекта: завершение job'а, перенос
находки в Отложено/Never из бота (см. app/bot/handlers/check.py).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Finding, FindingStatus, Project, Severity
from app.registry_store.store import read_registry
from app.tasks.project_context import local_path as project_local_path

_STATUS_BY_BUCKET = {
    "open": FindingStatus.OPEN,
    "later": FindingStatus.LATER,
    "never": FindingStatus.NEVER,
}

_KNOWN_SEVERITIES = {s.value for s in Severity}


def sync_project_findings(session: Session, project: Project) -> None:
    """Перечитывает .md-файлы проекта и приводит Finding-кэш в соответствие.

    Если у проекта нет local_path (нет локального чекаута) — ничего не
    делает: кэш нечем синкать, но и не затирает то, что там уже было
    записано раньше (когда local_path, возможно, был задан)."""
    path = project_local_path(project)
    if path is None:
        return

    registry = read_registry(path)
    existing = {
        f.file_symbol: f
        for f in session.scalars(select(Finding).where(Finding.project_id == project.id))
    }

    seen: set[str] = set()
    for bucket, items in (("open", registry.open), ("later", registry.later), ("never", registry.never)):
        status = _STATUS_BY_BUCKET[bucket]
        for rf in items:
            seen.add(rf.file_symbol)
            severity = Severity(rf.severity) if rf.severity in _KNOWN_SEVERITIES else None

            row = existing.get(rf.file_symbol)
            if row is None:
                row = Finding(project_id=project.id, file_symbol=rf.file_symbol)
                session.add(row)
                existing[rf.file_symbol] = row

            row.status = status
            row.severity = severity
            row.description = rf.description
            row.reason = rf.reason
            row.attempts = rf.attempts

    # Записи кэша, которых больше нет ни в одном .md — удаляем (находку
    # могли убрать из файла руками, вне бота).
    for file_symbol, row in existing.items():
        if file_symbol not in seen:
            session.delete(row)

    session.flush()
