"""_tick раньше не проверял, нет ли уже поставленной/выполняющейся
автопроверки, прежде чем поставить новую. Условие квоты (< порога) может
держаться часами, а тик срабатывает каждые 15 минут — без дедупа очередь
копила бы дубликат за дубликатом, пока условие не перестанет выполняться."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

import app.scheduler.autocheck as autocheck_module
from app.db.models import Job, JobStatus, Project, ProviderAccountStatus, ProviderMode, TaskType
from app.db.session import get_session
from app.providers.base import AuthStatus, QuotaEstimate
from app.scheduler.decision import AutocheckDecision


def _run(coro):
    return asyncio.run(coro)


class _FakeQuotaProvider:
    """Fake provider with controllable auth and quota status."""

    def __init__(self, name, *, used_pct=None, connected=True, disabled=False):
        self.name = name
        self._used_pct = used_pct
        self._connected = connected

    def auth_status(self):
        status = (
            ProviderAccountStatus.CONNECTED
            if self._connected
            else ProviderAccountStatus.NOT_CONNECTED
        )
        return AuthStatus(status=status)

    def estimate_quota(self):
        return QuotaEstimate(used_pct=self._used_pct, hours_to_reset=None)


class _FakeQuotaRegistry:
    """Fake registry with controllable providers and disabled set."""

    def __init__(self, providers: dict, disabled=None):
        self._providers = providers
        self._disabled = disabled or frozenset()

    def get(self, name):
        return self._providers[name]

    def is_disabled(self, name):
        return name in self._disabled


def _make_paused_quota_job(db, task_type: TaskType = TaskType.CHECK_FULL):
    """Create a job with PAUSED_QUOTA status."""
    with get_session() as session:
        job = Job(task_type=task_type, status=JobStatus.PAUSED_QUOTA, progress_total=1)
        session.add(job)
        session.flush()
        return job.id


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


def test_nightly_tick_noop_when_no_project_has_time_configured(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1"))

    application = _make_application(monkeypatch)
    monkeypatch.setattr(autocheck_module, "_now", lambda: datetime(2026, 8, 28, 3, 2))

    _run(autocheck_module._nightly_tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
    assert len(jobs) == 0


def test_nightly_tick_enqueues_job_when_time_matches_window(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", nightly_check_time="03:00"))

    application = _make_application(monkeypatch)
    monkeypatch.setattr(autocheck_module, "_now", lambda: datetime(2026, 8, 28, 3, 2))

    _run(autocheck_module._nightly_tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
        assert len(jobs) == 1
        assert jobs[0].task_type == TaskType.CHECK_FULL
        project = session.scalar(select(Project).where(Project.repo_full_name == "me/p1"))
        assert project.nightly_last_run_date == "2026-08-28"


def test_nightly_tick_does_not_double_enqueue_same_day(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", nightly_check_time="03:00"))

    application = _make_application(monkeypatch)
    monkeypatch.setattr(autocheck_module, "_now", lambda: datetime(2026, 8, 28, 3, 2))

    _run(autocheck_module._nightly_tick(application))
    with get_session() as session:
        job = session.scalar(select(Job).where(Job.provider_mode == ProviderMode.AUTO))
        job.status = JobStatus.DONE

    _run(autocheck_module._nightly_tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
    assert len(jobs) == 1


def test_nightly_tick_noop_when_time_does_not_match_window(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", nightly_check_time="03:00"))

    application = _make_application(monkeypatch)
    monkeypatch.setattr(autocheck_module, "_now", lambda: datetime(2026, 8, 28, 3, 20))

    _run(autocheck_module._nightly_tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
    assert len(jobs) == 0


def test_nightly_tick_skips_when_an_auto_job_is_already_pending(db, monkeypatch):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="me/p1", nightly_check_time="03:00"))
        session.add(
            Job(
                task_type=TaskType.CHECK_FULL,
                status=JobStatus.QUEUED,
                provider_mode=ProviderMode.AUTO,
                progress_total=1,
            )
        )

    application = _make_application(monkeypatch)
    monkeypatch.setattr(autocheck_module, "_now", lambda: datetime(2026, 8, 28, 3, 2))

    _run(autocheck_module._nightly_tick(application))

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.provider_mode == ProviderMode.AUTO)).all()
        assert len(jobs) == 1
        project = session.scalar(select(Project).where(Project.repo_full_name == "me/p1"))
        assert project.nightly_last_run_date is None


def test_resume_tick_resumes_when_first_chain_provider_available(db, monkeypatch):
    """When the first provider in the chain has available quota, job resumes."""
    monkeypatch.setattr(autocheck_module, "start_job", AsyncMock())

    job_id = _make_paused_quota_job(db, TaskType.CHECK_FULL)

    # Build providers for the first few in the chain (all with good quota)
    chain = autocheck_module.fallback_chain(TaskType.CHECK_FULL)
    providers = {
        name: _FakeQuotaProvider(name, used_pct=10) for name in chain[:3]
    }

    registry = _FakeQuotaRegistry(providers)
    application = SimpleNamespace(bot_data={"provider_registry": registry})

    _run(autocheck_module._resume_tick(application))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.QUEUED


def test_resume_tick_resumes_when_only_a_later_chain_provider_is_available(db, monkeypatch):
    """When only a later provider in chain has quota, job still resumes.
    This tests that we check the ENTIRE chain, not just job.provider."""
    monkeypatch.setattr(autocheck_module, "start_job", AsyncMock())

    job_id = _make_paused_quota_job(db, TaskType.CHECK_FULL)

    # Set up job to "remember" the first exhausted provider (simulating
    # that we ran on it before and hit quota)
    with get_session() as session:
        job = session.get(Job, job_id)
        chain = autocheck_module.fallback_chain(TaskType.CHECK_FULL)
        job.provider = chain[0]  # Remember first provider as exhausted
        session.commit()

    # Build providers: first few exhausted, last one available
    chain = autocheck_module.fallback_chain(TaskType.CHECK_FULL)
    providers = {}
    for i, name in enumerate(chain):
        if i < len(chain) - 1:
            # All but last: exhausted
            providers[name] = _FakeQuotaProvider(name, used_pct=99)
        else:
            # Last one: available
            providers[name] = _FakeQuotaProvider(name, used_pct=5)

    registry = _FakeQuotaRegistry(providers)
    application = SimpleNamespace(bot_data={"provider_registry": registry})

    _run(autocheck_module._resume_tick(application))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.QUEUED


def test_resume_tick_stays_paused_when_entire_chain_exhausted(db, monkeypatch):
    """When all providers in chain are exhausted, job remains PAUSED_QUOTA."""
    monkeypatch.setattr(autocheck_module, "start_job", AsyncMock())

    job_id = _make_paused_quota_job(db, TaskType.CHECK_FULL)

    chain = autocheck_module.fallback_chain(TaskType.CHECK_FULL)
    # All providers exhausted
    providers = {name: _FakeQuotaProvider(name, used_pct=99) for name in chain}

    registry = _FakeQuotaRegistry(providers)
    application = SimpleNamespace(bot_data={"provider_registry": registry})

    _run(autocheck_module._resume_tick(application))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.PAUSED_QUOTA


def test_resume_tick_skips_disabled_and_disconnected_providers(db, monkeypatch):
    """Disabled/disconnected providers are skipped even if they have good quota."""
    monkeypatch.setattr(autocheck_module, "start_job", AsyncMock())

    job_id = _make_paused_quota_job(db, TaskType.CHECK_FULL)

    chain = autocheck_module.fallback_chain(TaskType.CHECK_FULL)
    providers = {}
    disabled_set = set()

    for i, name in enumerate(chain):
        if i < len(chain) - 1:
            # All but last: exhausted OR disconnected/disabled
            if i == 0:
                # First one: disabled (even if it had good quota, it's disabled)
                providers[name] = _FakeQuotaProvider(name, used_pct=5)
                disabled_set.add(name)
            else:
                # Middle ones: disconnected or exhausted
                providers[name] = _FakeQuotaProvider(name, used_pct=99, connected=False)
        else:
            # Last one is also exhausted, so no provider is available
            providers[name] = _FakeQuotaProvider(name, used_pct=99)

    registry = _FakeQuotaRegistry(providers, disabled=disabled_set)
    application = SimpleNamespace(bot_data={"provider_registry": registry})

    _run(autocheck_module._resume_tick(application))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.PAUSED_QUOTA


def test_resume_tick_treats_missing_estimate_as_available(db, monkeypatch):
    """When used_pct=None (no estimate), treat as available and resume optimistically."""
    monkeypatch.setattr(autocheck_module, "start_job", AsyncMock())

    job_id = _make_paused_quota_job(db, TaskType.CHECK_FULL)

    chain = autocheck_module.fallback_chain(TaskType.CHECK_FULL)
    # All providers have no estimate (used_pct=None)
    providers = {name: _FakeQuotaProvider(name, used_pct=None) for name in chain}

    registry = _FakeQuotaRegistry(providers)
    application = SimpleNamespace(bot_data={"provider_registry": registry})

    _run(autocheck_module._resume_tick(application))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.QUEUED
