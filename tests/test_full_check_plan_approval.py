from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db.models import Job, Project, ProviderAccountStatus, ProviderMode, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AIProvider, AuthStatus, ProviderResult
from app.tasks import clarify
from app.tasks.pipeline import StepContext
from app.tasks.protocol_full import Step5FleetPlanner


class DomainsProvider(AIProvider):
    name = ProviderName.CLAUDE

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None) -> ProviderResult:
        return ProviderResult(text="auth\napi\ndb")


def _make_job_and_project(session, *, provider_mode: ProviderMode, created_by_tg_id: int | None = None):
    project = Project(name="P", repo_full_name="owner/p")
    session.add(project)
    session.flush()
    job = Job(
        task_type=TaskType.CHECK_FULL,
        provider_mode=provider_mode,
        created_by_tg_id=created_by_tg_id,
        progress_total=13,
    )
    job.projects = [project]
    session.add(job)
    session.flush()
    return job, project


def test_plan_approval_skipped_for_auto_jobs(db):
    with get_session() as session:
        job, project = _make_job_and_project(session, provider_mode=ProviderMode.AUTO, created_by_tg_id=555)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        ctx = StepContext(
            job=job, projects=[project], provider=DomainsProvider(), session=session, application=application
        )

        Step5FleetPlanner().run(ctx)

        assert ctx.state["domains"] == ["auth", "api", "db"]
        application.bot.send_message.assert_not_called()


def test_plan_approval_skipped_without_application(db):
    with get_session() as session:
        job, project = _make_job_and_project(session, provider_mode=ProviderMode.MANUAL, created_by_tg_id=555)
        ctx = StepContext(job=job, projects=[project], provider=DomainsProvider(), session=session)

        Step5FleetPlanner().run(ctx)

        assert ctx.state["domains"] == ["auth", "api", "db"]


def test_plan_approval_default_ok_word_keeps_domains(db, monkeypatch):
    with get_session() as session:
        job, project = _make_job_and_project(session, provider_mode=ProviderMode.MANUAL, created_by_tg_id=555)
        session.commit()
        job_id = job.id

        def fake_sleep(_):
            clarify.answer(job_id, "да")

        monkeypatch.setattr(clarify.time, "sleep", fake_sleep)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        ctx = StepContext(
            job=job, projects=[project], provider=DomainsProvider(), session=session, application=application
        )

        Step5FleetPlanner().run(ctx)

        assert ctx.state["domains"] == ["auth", "api", "db"]
        application.bot.send_message.assert_awaited_once()


def test_plan_approval_custom_answer_overrides_domains(db, monkeypatch):
    with get_session() as session:
        job, project = _make_job_and_project(session, provider_mode=ProviderMode.MANUAL, created_by_tg_id=555)
        session.commit()
        job_id = job.id

        def fake_sleep(_):
            clarify.answer(job_id, "payments, billing")

        monkeypatch.setattr(clarify.time, "sleep", fake_sleep)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        ctx = StepContext(
            job=job, projects=[project], provider=DomainsProvider(), session=session, application=application
        )

        Step5FleetPlanner().run(ctx)

        assert ctx.state["domains"] == ["payments", "billing"]


def test_plan_approval_timeout_falls_back_to_planner_domains(db, monkeypatch):
    with get_session() as session:
        job, project = _make_job_and_project(session, provider_mode=ProviderMode.MANUAL, created_by_tg_id=555)
        session.commit()

        monkeypatch.setattr(clarify, "DEFAULT_TIMEOUT_SECONDS", 0)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        ctx = StepContext(
            job=job, projects=[project], provider=DomainsProvider(), session=session, application=application
        )

        Step5FleetPlanner().run(ctx)

        assert ctx.state["domains"] == ["auth", "api", "db"]
