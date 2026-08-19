"""_tick раньше не проверял, нет ли уже поставленной/выполняющейся
автопроверки, прежде чем поставить новую. Условие квоты (< порога) может
держаться часами, а тик срабатывает каждые 15 минут — без дедупа очередь
копила бы дубликат за дубликатом, пока условие не перестанет выполняться."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

import app.scheduler.autocheck as autocheck_module
from app.db.models import Job, JobStatus, Project, ProviderMode, TaskType
from app.db.session import get_session
from app.scheduler.decision import AutocheckDecision


def _run(coro):
    return asyncio.run(coro)


def _make_application(monkeypatch, admin_tg_id: int = 1):
    monkeypatch.setattr(
        autocheck_module,
        "decide_autocheck_action",
        lambda *a, **kw: AutocheckDecision(
            would_run=True, task_type=TaskType.CHECK_FULL, reason="test forces a run"
        ),
    )
    monkeypatch.setattr(autocheck_module, "start_job", AsyncMock())

    settings = SimpleNamespace(admin_tg_id=admin_tg_id, autocheck=SimpleNamespace(enabled=True))
    application = SimpleNamespace(
        bot_data={"settings": settings, "provider_registry": SimpleNamespace()}
    )
    return application


def test_tick_enqueues_one_job_when_none_pending(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", autocheck_enabled=True))

    application = _make_application(monkeypatch)

    _run(autocheck_module._tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
    assert len(jobs) == 1


def test_tick_skips_enqueue_when_an_auto_job_is_already_pending(db, monkeypatch):
    """Ключевой сценарий бага: два тика подряд с тем же истинным decision
    не должны давать два job'а, пока первый ещё не завершён."""
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", autocheck_enabled=True))

    application = _make_application(monkeypatch)

    _run(autocheck_module._tick(application))
    _run(autocheck_module._tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
    assert len(jobs) == 1


def test_tick_enqueues_again_once_previous_auto_job_is_done(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", autocheck_enabled=True))

    application = _make_application(monkeypatch)

    _run(autocheck_module._tick(application))

    with get_session() as session:
        job = session.scalar(select(Job).where(Job.provider_mode == ProviderMode.AUTO))
        job.status = JobStatus.DONE

    _run(autocheck_module._tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
    assert len(jobs) == 2
