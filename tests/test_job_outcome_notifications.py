"""_deliver_outcome теперь дублирует тот же текст в Slack/Discord, если
настроены (см. app.notifications.webhook.notify_external) — best-effort,
после доставки в Telegram, никогда не вместо неё."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.bot.job_runner as job_runner_module
from app.db.models import Job, JobStatus, TaskType
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _make_done_job(session) -> int:
    job = Job(task_type=TaskType.FIX, status=JobStatus.DONE, progress_total=1, report_text="ok")
    session.add(job)
    session.flush()
    return job.id


def _application(settings):
    send_message = AsyncMock()
    return SimpleNamespace(bot=SimpleNamespace(send_message=send_message), bot_data={"settings": settings})


def test_no_webhooks_configured_skips_notify_external(db, monkeypatch):
    with get_session() as session:
        job_id = _make_done_job(session)
    notify = AsyncMock()
    monkeypatch.setattr(job_runner_module, "notify_external", notify)
    from app.config import NotificationSettings

    application = _application(SimpleNamespace(notifications=NotificationSettings()))

    _run(job_runner_module._deliver_outcome(application, job_id, 555, JobStatus.DONE, None))

    notify.assert_not_awaited()


def test_slack_webhook_configured_calls_notify_external(db, monkeypatch):
    with get_session() as session:
        job_id = _make_done_job(session)
    notify = AsyncMock()
    monkeypatch.setattr(job_runner_module, "notify_external", notify)
    from app.config import NotificationSettings

    application = _application(
        SimpleNamespace(notifications=NotificationSettings(slack_webhook_url="https://hooks.slack.com/x"))
    )

    _run(job_runner_module._deliver_outcome(application, job_id, 555, JobStatus.DONE, None))

    notify.assert_awaited_once()
    args, kwargs = notify.await_args
    assert kwargs["slack_webhook_url"] == "https://hooks.slack.com/x"
    assert kwargs["discord_webhook_url"] is None


def test_missing_bot_data_does_not_crash(db, monkeypatch):
    """Оборонительный getattr — тестовые двойники (см. test_job_start_approval.py)
    часто не заводят bot_data вообще, это не должно ронять доставку отчёта."""
    with get_session() as session:
        job_id = _make_done_job(session)
    notify = AsyncMock()
    monkeypatch.setattr(job_runner_module, "notify_external", notify)
    send_message = AsyncMock()
    application = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))

    _run(job_runner_module._deliver_outcome(application, job_id, 555, JobStatus.DONE, None))

    send_message.assert_awaited_once()
    notify.assert_not_awaited()
