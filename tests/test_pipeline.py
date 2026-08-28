from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import Job, JobStatus, Project, TaskType
from app.db.session import get_session
from app.providers.base import ProviderQuotaExceededError
from app.tasks import clarify
from app.tasks.pipeline import Pipeline, PipelineCancelled, PipelineInterrupted, Step, StepContext
from app.tasks.queue import JobQueue


class RecordingStep(Step):
    def __init__(self, label: str, calls: list[str], *, raise_quota: bool = False) -> None:
        self.label = label
        self._calls = calls
        self._raise_quota = raise_quota

    def run(self, ctx: StepContext) -> None:
        self._calls.append(self.label)
        if self._raise_quota:
            raise ProviderQuotaExceededError("нет квоты")
        ctx.state[self.label] = f"результат {self.label}"


def _make_job(session) -> Job:
    project = Project(name="P1", repo_full_name="owner/p1")
    session.add(project)
    session.flush()
    job = Job(task_type=TaskType.CUSTOM, progress_total=0)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job


def test_pipeline_runs_all_steps_and_marks_done(db):
    calls: list[str] = []
    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(job=job, projects=list(job.projects), provider=None, session=session)
        pipeline = Pipeline([RecordingStep("a", calls), RecordingStep("b", calls)])

        pipeline.run(ctx, queue)

        assert calls == ["a", "b"]
        assert job.status == JobStatus.DONE
        assert job.progress_step == 2
        assert job.progress_total == 2


def test_pipeline_quota_exceeded_pauses_job(db):
    calls: list[str] = []
    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(job=job, projects=list(job.projects), provider=None, session=session)
        pipeline = Pipeline(
            [
                RecordingStep("a", calls),
                RecordingStep("b", calls, raise_quota=True),
                RecordingStep("c", calls),
            ]
        )

        with pytest.raises(PipelineInterrupted):
            pipeline.run(ctx, queue)

        assert calls == ["a", "b"]  # "c" не должен был выполниться
        assert job.status == JobStatus.PAUSED_QUOTA
        assert "2/3" in job.handover_note


def test_pipeline_resume_skips_done_steps_and_restores_state(db):
    """Резюме после HANDOVER (job.progress_step > 0, новый StepContext с
    пустым ctx.state — ровно так, как это происходит в реальности: каждый
    start_job() строит StepContext заново, см. app.bot.job_runner). Шаг "a"
    не должен выполниться повторно, а его результат должен быть виден шагу
    "c" через восстановленный ctx.state — иначе резюме "работает", но
    молча теряет данные всех уже пройденных шагов."""
    calls: list[str] = []
    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(job=job, projects=list(job.projects), provider=None, session=session)
        pipeline = Pipeline(
            [
                RecordingStep("a", calls),
                RecordingStep("b", calls, raise_quota=True),
                RecordingStep("c", calls),
            ]
        )

        with pytest.raises(PipelineInterrupted):
            pipeline.run(ctx, queue)

        assert calls == ["a", "b"]  # "b" начал выполняться, но упал до записи в state
        assert job.progress_step == 1  # только "a" зачтён как завершённый шаг
        assert job.state_json is not None

    # Симулируем реальный поток: квота сброшена, scheduler переводит job
    # обратно в QUEUED, start_job() собирает пайплайн и StepContext заново.
    calls.clear()
    with get_session() as session:
        job2 = session.get(Job, job.id)
        queue2 = JobQueue(session)
        resumed_ctx = StepContext(job=job2, projects=list(job2.projects), provider=None, session=session)
        resumed_pipeline = Pipeline(
            [RecordingStep("a", calls), RecordingStep("b", calls), RecordingStep("c", calls)]
        )

        resumed_pipeline.run(resumed_ctx, queue2)

        assert calls == ["b", "c"]  # "a" НЕ выполнился повторно
        assert resumed_ctx.state["a"] == "результат a"  # восстановлено из state_json
        assert resumed_ctx.state["b"] == "результат b"
        assert resumed_ctx.state["c"] == "результат c"
        assert job2.status == JobStatus.DONE
        assert job2.progress_step == 3


def test_pipeline_resume_does_not_persist_or_crash_on_underscore_state(db):
    """ctx.state["_x"] — конвенция для служебных рантайм-объектов (см.
    tiers.py::run_prompt_with_tier -> "_tier_picker", живой TierPicker,
    не JSON-совместимый). Такой ключ не должен попасть в state_json и не
    должен пережить резюме — иначе на резюме код увидел бы там мусорную
    строку (json.dumps(..., default=str)) вместо ожидаемого None/объекта."""

    class StepWithPrivateState(Step):
        label = "private"

        def run(self, ctx: StepContext) -> None:
            ctx.state["_runtime_only"] = object()  # не JSON-сериализуем в принципе
            ctx.state["public"] = "видимое значение"

    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(job=job, projects=list(job.projects), provider=None, session=session)
        pipeline = Pipeline([StepWithPrivateState(), RecordingStep("b", [], raise_quota=True)])

        with pytest.raises(PipelineInterrupted):
            pipeline.run(ctx, queue)

        assert "_runtime_only" not in job.state_json
        assert "public" in job.state_json

    with get_session() as session:
        job2 = session.get(Job, job.id)
        queue2 = JobQueue(session)
        resumed_ctx = StepContext(job=job2, projects=list(job2.projects), provider=None, session=session)
        resumed_pipeline = Pipeline([StepWithPrivateState(), RecordingStep("b", [])])

        resumed_pipeline.run(resumed_ctx, queue2)

        assert "_runtime_only" not in resumed_ctx.state  # не восстановлен как строка-мусор
        assert resumed_ctx.state["public"] == "видимое значение"
        assert job2.status == JobStatus.DONE


def test_pipeline_cancel_requested_stops_early(db):
    calls: list[str] = []
    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(
            job=job,
            projects=list(job.projects),
            provider=None,
            session=session,
            cancel_requested=lambda: len(calls) >= 1,
        )
        pipeline = Pipeline([RecordingStep("a", calls), RecordingStep("b", calls)])

        with pytest.raises(PipelineCancelled):
            pipeline.run(ctx, queue)

        assert calls == ["a"]
        assert job.status == JobStatus.CANCELLED


def test_pipeline_pause_blocks_then_resumes(db, monkeypatch):
    """Пауза между шагами: job уходит в PAUSED_MANUAL, движок реально
    блокируется (проверяем, что poll вызывается), затем при снятии флага
    паузы возвращается в RUNNING и шаг всё-таки выполняется."""
    monkeypatch.setattr("app.tasks.pipeline.time.sleep", lambda _: None)

    calls: list[str] = []
    poll_count = {"n": 0}

    def paused_requested() -> bool:
        poll_count["n"] += 1
        return poll_count["n"] <= 3  # "снимаем" паузу на 4-й проверке

    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(
            job=job,
            projects=list(job.projects),
            provider=None,
            session=session,
            paused_requested=paused_requested,
        )
        pipeline = Pipeline([RecordingStep("a", calls)])

        pipeline.run(ctx, queue)

        assert calls == ["a"]
        assert job.status == JobStatus.DONE  # успел доехать до конца после снятия паузы
        assert poll_count["n"] >= 3


def test_pipeline_cancel_while_paused_ends_cancelled(db, monkeypatch):
    monkeypatch.setattr("app.tasks.pipeline.time.sleep", lambda _: None)
    calls: list[str] = []
    state = {"cancelled": False}

    def paused_requested() -> bool:
        state["cancelled"] = True  # на первой же проверке паузы решаем отменить
        return True

    with get_session() as session:
        job = _make_job(session)
        queue = JobQueue(session)
        ctx = StepContext(
            job=job,
            projects=list(job.projects),
            provider=None,
            session=session,
            paused_requested=paused_requested,
            cancel_requested=lambda: state["cancelled"],
        )
        pipeline = Pipeline([RecordingStep("a", calls)])

        with pytest.raises(PipelineCancelled):
            pipeline.run(ctx, queue)

        assert calls == []  # шаг так и не выполнился
        assert job.status == JobStatus.CANCELLED


def test_step_context_ask_user_returns_none_without_application(db):
    with get_session() as session:
        job = _make_job(session)
        ctx = StepContext(job=job, projects=list(job.projects), provider=None, session=session)

        assert ctx.ask_user("Нужно уточнение?") is None


def test_step_context_ask_user_delegates_to_clarify_ask_and_wait(db, monkeypatch):
    with get_session() as session:
        job = _make_job(session)
        job.created_by_tg_id = 555
        session.commit()
        job_id = job.id

        def fake_sleep(_):
            clarify.answer(job_id, "ответ пользователя")

        monkeypatch.setattr(clarify.time, "sleep", fake_sleep)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        ctx = StepContext(
            job=job,
            projects=list(job.projects),
            provider=None,
            session=session,
            application=application,
        )

        answer = ctx.ask_user("Уточни, пожалуйста")

        assert answer == "ответ пользователя"
        application.bot.send_message.assert_awaited_once()
        sent_chat_id = application.bot.send_message.call_args[0][0]
        assert sent_chat_id == 555


class LiveNoteInjectingStep(Step):
    label = "inject"

    def __init__(self, job_id: int, text: str) -> None:
        self._job_id = job_id
        self._text = text

    def run(self, ctx: StepContext) -> None:
        with get_session() as session:
            other_job = session.get(Job, self._job_id)
            JobQueue(session).add_live_note(other_job, self._text)


class CommentCapturingStep(Step):
    label = "capture"

    def __init__(self, captured: list[str | None]) -> None:
        self._captured = captured

    def run(self, ctx: StepContext) -> None:
        self._captured.append(ctx.comment)


def test_pipeline_refreshes_live_notes_added_from_other_session_and_prepends_comment(db):
    captured: list[str | None] = []
    with get_session() as session:
        job = _make_job(session)
        job.comment = "Изначальная задача"
        session.commit()
        job_id = job.id
        queue = JobQueue(session)
        ctx = StepContext(
            job=job, projects=list(job.projects), provider=None, session=session, comment=job.comment
        )
        pipeline = Pipeline(
            [LiveNoteInjectingStep(job_id, "добавь логирование"), CommentCapturingStep(captured)]
        )

        pipeline.run(ctx, queue)

        assert len(captured) == 1
        assert ctx.job.live_notes is not None
        assert ctx.job.live_notes.startswith("[")
        assert ctx.job.live_notes.endswith("добавь логирование")
        assert captured[0] == (
            f"Изначальная задача\n\nДополнения пользователя во время выполнения:\n{ctx.job.live_notes}"
        )


def test_pipeline_rebuilds_comment_with_live_notes_only_when_no_original_comment(db):
    captured: list[str | None] = []
    with get_session() as session:
        job = _make_job(session)
        session.commit()
        job_id = job.id
        queue = JobQueue(session)
        ctx = StepContext(job=job, projects=list(job.projects), provider=None, session=session)
        pipeline = Pipeline(
            [LiveNoteInjectingStep(job_id, "срочно добавь тесты"), CommentCapturingStep(captured)]
        )

        pipeline.run(ctx, queue)

        assert ctx.job.live_notes is not None
        assert captured[0] == f"Дополнения пользователя во время выполнения:\n{ctx.job.live_notes}"


def test_pipeline_leaves_comment_unchanged_when_no_live_notes(db):
    captured: list[str | None] = []
    with get_session() as session:
        job = _make_job(session)
        job.comment = "Просто задача"
        session.commit()
        queue = JobQueue(session)
        ctx = StepContext(
            job=job, projects=list(job.projects), provider=None, session=session, comment=job.comment
        )
        pipeline = Pipeline([CommentCapturingStep(captured), CommentCapturingStep(captured)])

        pipeline.run(ctx, queue)

        assert captured == ["Просто задача", "Просто задача"]
