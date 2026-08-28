from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db.models import Job, Project, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AIProvider, AuthStatus, ProviderResult
from app.tasks import clarify
from app.tasks.generic import GenericStep1Plan
from app.tasks.pipeline import StepContext


class QueuedProvider(AIProvider):
    name = ProviderName.CLAUDE

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None) -> ProviderResult:
        self.prompts.append(prompt)
        return ProviderResult(text=self._responses.pop(0))


def _make_job_and_project(session, *, created_by_tg_id: int | None = None):
    project = Project(name="P", repo_full_name="owner/p")
    session.add(project)
    session.flush()
    job = Job(task_type=TaskType.FEATURE, created_by_tg_id=created_by_tg_id, progress_total=4)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job, project


def test_generic_step1_plan_uses_plain_plan_when_no_question(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = QueuedProvider(["1. Сделай A\n2. Сделай B"])
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session, comment="фича X")

        GenericStep1Plan().run(ctx)

        assert ctx.state["plan"] == "1. Сделай A\n2. Сделай B"
        assert len(provider.prompts) == 1


def test_generic_step1_plan_marker_detection_is_case_insensitive_and_unanswered(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = QueuedProvider(["вопрос: нужен ли кэш?"])
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session, comment="фича Y")

        GenericStep1Plan().run(ctx)

        assert "нужен ли кэш?" in ctx.state["plan"]
        assert "без ответа" in ctx.state["plan"]
        assert len(provider.prompts) == 1


def test_generic_step1_plan_falls_back_to_noting_unanswered_question_without_application(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = QueuedProvider(["ВОПРОС: Нужна ли обратная совместимость?"])
        ctx = StepContext(
            job=job, projects=[project], provider=provider, session=session, comment="рефактори модуль X"
        )

        GenericStep1Plan().run(ctx)

        assert "Нужна ли обратная совместимость?" in ctx.state["plan"]
        assert "без ответа" in ctx.state["plan"]
        assert len(provider.prompts) == 1


def test_generic_step1_plan_asks_user_and_replans_when_answered(db, monkeypatch):
    with get_session() as session:
        job, project = _make_job_and_project(session, created_by_tg_id=555)
        session.commit()
        job_id = job.id

        provider = QueuedProvider(
            [
                "ВОПРОС: Какой формат даты нужен?",
                "1. Используем ISO 8601\n2. Пиши парсер",
            ]
        )

        def fake_sleep(_):
            clarify.answer(job_id, "ISO 8601")

        monkeypatch.setattr(clarify.time, "sleep", fake_sleep)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=provider,
            session=session,
            comment="добавь фичу с датами",
            application=application,
        )

        GenericStep1Plan().run(ctx)

        assert ctx.state["plan"] == "1. Используем ISO 8601\n2. Пиши парсер"
        assert len(provider.prompts) == 2
        assert "Какой формат даты нужен?" in provider.prompts[1]
        assert "ISO 8601" in provider.prompts[1]


def test_generic_step1_plan_no_question_never_touches_ask_user(db):
    with get_session() as session:
        job, project = _make_job_and_project(session, created_by_tg_id=555)
        provider = QueuedProvider(["1. Просто план без вопросов"])
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=provider,
            session=session,
            comment="фича Z",
            application=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
        )

        GenericStep1Plan().run(ctx)

        assert ctx.state["plan"] == "1. Просто план без вопросов"
        ctx.application.bot.send_message.assert_not_called()
