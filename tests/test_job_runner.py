from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.job_runner import _deliver_outcome, _run_pipeline_blocking, _send_handoff_document
from app.db.models import HistoryEntry, Job, JobStatus, Project, ProviderMode, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AuthStatus, ProviderAccountStatus, ProviderQuotaExceededError, ProviderResult
from app.providers.registry import ProviderRegistry


class _FailingProvider:
    def __init__(self, name):
        self.name = name

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None):
        raise ProviderQuotaExceededError(f"{self.name.value}: квота исчерпана")


class _SucceedingProvider:
    def __init__(self, name, text="ok"):
        self.name = name
        self._text = text
        self.calls = 0

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None):
        self.calls += 1
        return ProviderResult(text=self._text)


def test_mid_run_provider_switch_is_visible_in_history_entry(db):
    with get_session() as session:
        project = Project(name="P", repo_full_name="o/p")
        session.add(project)
        session.flush()
        job = Job(
            task_type=TaskType.CUSTOM,
            provider=ProviderName.CLAUDE_CODE,
            provider_mode=ProviderMode.MANUAL,
            comment="сделай штуку",
            progress_total=0,
        )
        job.projects = [project]
        session.add(job)
        session.flush()
        job_id = job.id
        project_id = project.id

    registry = ProviderRegistry(
        {
            ProviderName.CLAUDE_CODE: _FailingProvider(ProviderName.CLAUDE_CODE),
            ProviderName.CLAUDE: _SucceedingProvider(ProviderName.CLAUDE, text="unified diff тут"),
        }
    )
    application = SimpleNamespace(bot_data={"provider_registry": registry})

    _run_pipeline_blocking(application, job_id)

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.DONE
        assert job.provider == ProviderName.CLAUDE

        history = session.query(HistoryEntry).filter_by(job_id=job_id, project_id=project_id).one()
        assert history.provider == ProviderName.CLAUDE


def test_deliver_outcome_archive_shows_archive_text_not_cancelled(db):
    with get_session() as session:
        job = Job(task_type=TaskType.CUSTOM, status=JobStatus.CANCELLED, progress_total=1)
        session.add(job)
        session.flush()
        job_id = job.id

    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot, bot_data={})

    asyncio.run(_deliver_outcome(application, job_id, 123, JobStatus.CANCELLED, None, is_archive=True))

    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.call_args
    assert "рхив" in args[1]
    assert "Отменено" not in args[1]


def test_deliver_outcome_plain_cancel_without_archive_flag(db):
    with get_session() as session:
        job = Job(task_type=TaskType.CUSTOM, status=JobStatus.CANCELLED, progress_total=1)
        session.add(job)
        session.flush()
        job_id = job.id

    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot, bot_data={})

    asyncio.run(_deliver_outcome(application, job_id, 123, JobStatus.CANCELLED, None, is_archive=False))

    args, kwargs = bot.send_message.call_args
    assert "Отменено" in args[1]


def test_send_handoff_document_includes_report_and_comment(db):
    with get_session() as session:
        project = Project(name="P", repo_full_name="o/p")
        session.add(project)
        session.flush()
        job = Job(
            task_type=TaskType.CUSTOM,
            status=JobStatus.CANCELLED,
            comment="сделай штуку",
            report_text="частичный план",
            progress_total=4,
            progress_step=1,
        )
        job.projects = [project]
        session.add(job)
        session.flush()
        job_id = job.id

    bot = SimpleNamespace(send_document=AsyncMock())
    application = SimpleNamespace(bot=bot, bot_data={})

    asyncio.run(_send_handoff_document(application, job_id, 123))

    bot.send_document.assert_awaited_once()
    _, kwargs = bot.send_document.call_args
    body = kwargs["document"].decode("utf-8")
    assert "сделай штуку" in body
    assert "частичный план" in body
    assert kwargs["filename"] == f"job_{job_id}_handoff.md"
