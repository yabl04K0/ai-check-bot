from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db.models import Job, JobStatus, TaskType
from app.db.session import get_session
from app.tasks import clarify


def _make_job(status: JobStatus = JobStatus.RUNNING, created_by_tg_id: int | None = 555) -> int:
    with get_session() as session:
        job = Job(
            task_type=TaskType.CUSTOM,
            status=status,
            created_by_tg_id=created_by_tg_id,
            progress_total=1,
        )
        session.add(job)
        session.flush()
        return job.id


def _fake_application() -> SimpleNamespace:
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


def test_ask_and_wait_returns_none_immediately_when_chat_id_none(db):
    job_id = _make_job()
    application = _fake_application()

    result = clarify.ask_and_wait(application, job_id, None, "Вопрос?")

    assert result is None
    application.bot.send_message.assert_not_called()
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.RUNNING
        assert job.pending_question is None


def test_ask_and_wait_returns_none_when_job_missing(db):
    application = _fake_application()

    result = clarify.ask_and_wait(application, 999999, 555, "Вопрос?")

    assert result is None
    application.bot.send_message.assert_not_called()


def test_ask_and_wait_sets_pending_question_and_pauses_status_then_answers(db, monkeypatch):
    job_id = _make_job()
    application = _fake_application()

    def fake_sleep(_):
        with get_session() as session:
            mid_job = session.get(Job, job_id)
            assert mid_job.status == JobStatus.PAUSED_QUESTION
            assert mid_job.pending_question == "Уточни, пожалуйста"
        clarify.answer(job_id, "ответ пользователя")

    monkeypatch.setattr(clarify.time, "sleep", fake_sleep)

    result = clarify.ask_and_wait(application, job_id, 555, "Уточни, пожалуйста")

    assert result == "ответ пользователя"
    application.bot.send_message.assert_awaited_once()
    sent_chat_id, sent_text = application.bot.send_message.call_args[0]
    assert sent_chat_id == 555
    assert "Уточни, пожалуйста" in sent_text
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.RUNNING
        assert job.pending_question is None
    assert clarify.has_pending(job_id) is False


def test_ask_and_wait_times_out_and_reverts_status(db, monkeypatch):
    job_id = _make_job()
    application = _fake_application()
    monkeypatch.setattr(clarify, "POLL_SECONDS", 1)
    monkeypatch.setattr(clarify.time, "sleep", lambda _: None)

    result = clarify.ask_and_wait(application, job_id, 555, "Вопрос?", timeout=2)

    assert result is None
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.RUNNING
        assert job.pending_question is None
    assert clarify.has_pending(job_id) is False


def test_ask_and_wait_returns_none_when_cancelled_mid_wait(db, monkeypatch):
    job_id = _make_job()
    application = _fake_application()
    monkeypatch.setattr(clarify.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def cancel_requested() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    result = clarify.ask_and_wait(application, job_id, 555, "Вопрос?", cancel_requested=cancel_requested)

    assert result is None
    assert calls["n"] >= 2
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.RUNNING
        assert job.pending_question is None


def test_ask_and_wait_returns_none_and_reverts_status_when_send_message_fails(db):
    job_id = _make_job()
    application = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))
    )

    result = clarify.ask_and_wait(application, job_id, 555, "Вопрос?")

    assert result is None
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.RUNNING
        assert job.pending_question is None
    assert clarify.has_pending(job_id) is False


def test_ask_and_wait_does_not_override_status_changed_elsewhere_during_wait(db, monkeypatch):
    job_id = _make_job()
    application = _fake_application()

    def fake_sleep(_):
        with get_session() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.CANCELLED
        clarify.answer(job_id, "ответ")

    monkeypatch.setattr(clarify.time, "sleep", fake_sleep)

    result = clarify.ask_and_wait(application, job_id, 555, "Вопрос?")

    assert result == "ответ"
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.CANCELLED
        assert job.pending_question is None


def test_has_pending_false_before_ask_and_wait_starts(db):
    job_id = _make_job()
    assert clarify.has_pending(job_id) is False


def test_answer_returns_false_when_job_not_waiting(db):
    job_id = _make_job()
    assert clarify.answer(job_id, "текст") is False
