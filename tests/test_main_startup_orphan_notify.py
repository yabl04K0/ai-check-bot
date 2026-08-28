"""_on_startup — задачи, зависшие с прошлого запуска (RUNNING/PAUSED_MANUAL,
см. JobQueue.reconcile_orphaned), теперь не просто тихо помечаются error в
БД, а ещё и уходят владельцу уведомлением в Telegram. Раньше уведомления
не было вообще, только logger.warning — пользователь не понимал, почему
задача, честно резюмировавшаяся после сброса квоты и работавшая часами,
"пропадала" без единого сообщения в чат, если процесс бота убивали
рестартом до её завершения (живой случай: job #17)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.main as main_module
from app.db.models import Job, JobStatus, TaskType
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _stub_side_effects(monkeypatch):
    monkeypatch.setattr(main_module, "restart_bridge", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "seed_default_tier", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "start_scheduler", lambda application: SimpleNamespace())
    monkeypatch.setattr(main_module, "register_proxy_maintenance", lambda *a, **k: None)


def _application(send_message: AsyncMock, admin_tg_id: int = 42) -> SimpleNamespace:
    settings = SimpleNamespace(admin_tg_id=admin_tg_id, db_path=Path("dummy.sqlite3"))
    return SimpleNamespace(
        bot_data={"settings": settings},
        bot=SimpleNamespace(send_message=send_message),
    )


def test_on_startup_notifies_admin_about_orphaned_jobs(db, monkeypatch):
    with get_session() as session:
        job = Job(task_type=TaskType.FIX, status=JobStatus.RUNNING)
        session.add(job)
        session.flush()
        job_id = job.id

    _stub_side_effects(monkeypatch)
    send_message = AsyncMock()
    application = _application(send_message)

    _run(main_module._on_startup(application))

    send_message.assert_awaited_once()
    args, kwargs = send_message.await_args
    assert args[0] == 42
    assert f"#{job_id}" in args[1]

    with get_session() as session:
        reloaded = session.get(Job, job_id)
        assert reloaded.status == JobStatus.ERROR


def test_on_startup_does_not_notify_when_nothing_orphaned(db, monkeypatch):
    _stub_side_effects(monkeypatch)
    send_message = AsyncMock()
    application = _application(send_message)

    _run(main_module._on_startup(application))

    send_message.assert_not_awaited()


def test_on_startup_skips_notification_without_admin_tg_id(db, monkeypatch):
    with get_session() as session:
        session.add(Job(task_type=TaskType.FIX, status=JobStatus.RUNNING))

    _stub_side_effects(monkeypatch)
    send_message = AsyncMock()
    application = _application(send_message, admin_tg_id=None)

    _run(main_module._on_startup(application))

    send_message.assert_not_awaited()
