from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.ai_chat import agent_activity
from app.bot.handlers.menu import activity_text
from app.db.models import AiChatSession, Job, JobStatus, TaskType
from app.db.session import get_session


def _context():
    return SimpleNamespace(application=SimpleNamespace(bot_data={}))


def test_activity_text_reports_empty_state(db):
    text = activity_text(_context())

    assert "Нет активных задач." in text
    assert "Нет запущенных агентов." in text
    assert "Нет активных ИИ-чатов." in text


def test_activity_text_shows_running_job_with_progress(db):
    with get_session() as session:
        session.add(
            Job(task_type=TaskType.CHECK_FULL, status=JobStatus.RUNNING, progress_step=3, progress_total=12)
        )

    text = activity_text(_context())

    assert "Нет активных задач." not in text
    assert "3/12" in text


def test_activity_text_counts_paused_question_job_as_active(db):
    with get_session() as session:
        session.add(
            Job(
                task_type=TaskType.FIX,
                status=JobStatus.PAUSED_QUESTION,
                progress_step=1,
                progress_total=4,
                pending_question="Продолжать?",
            )
        )

    text = activity_text(_context())

    assert "Нет активных задач." not in text


def test_activity_text_excludes_finished_jobs(db):
    with get_session() as session:
        session.add(
            Job(task_type=TaskType.CHECK_FULL, status=JobStatus.DONE, progress_step=12, progress_total=12)
        )
        session.add(
            Job(task_type=TaskType.FIX, status=JobStatus.CANCELLED, progress_step=1, progress_total=4)
        )
        session.add(Job(task_type=TaskType.FIX, status=JobStatus.ERROR, progress_step=1, progress_total=4))

    text = activity_text(_context())

    assert "Нет активных задач." in text


def test_activity_text_shows_active_native_agent(db):
    activity_id = agent_activity.start("demo-project", "чинит баг в модуле авторизации")
    try:
        text = activity_text(_context())
    finally:
        agent_activity.finish(activity_id)

    assert "Нет запущенных агентов." not in text
    assert "demo-project" in text
    assert "чинит баг в модуле авторизации" in text


def test_activity_text_truncates_long_task_snippet(db):
    long_task = "x" * 200
    activity_id = agent_activity.start("demo-project", long_task)
    try:
        text = activity_text(_context())
    finally:
        agent_activity.finish(activity_id)

    assert "x" * 200 not in text
    assert "x" * 60 in text


def test_activity_text_shows_open_chat_with_live_status(db):
    with get_session() as session:
        session.add(
            AiChatSession(
                tg_user_id="123",
                full_access=True,
                status_detail="🧠 думает…",
                closed_at=None,
            )
        )

    text = activity_text(_context())

    assert "Нет активных ИИ-чатов." not in text
    assert "думает" in text


def test_activity_text_excludes_closed_chat_even_with_status_detail(db):
    with get_session() as session:
        session.add(
            AiChatSession(
                tg_user_id="123",
                full_access=True,
                status_detail="🧠 думает…",
                closed_at=datetime.now(timezone.utc),
            )
        )

    text = activity_text(_context())

    assert "Нет активных ИИ-чатов." in text
