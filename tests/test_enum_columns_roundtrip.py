"""Регресс на баг: enum-колонки, объявленные как mapped_column(String(N)),
хранили и читали данные без проблем, но SQLAlchemy НЕ оборачивал их
обратно в Python enum при загрузке в новой сессии — Job.task_type после
session.get() в свежей сессии был голым str без .value. Это ловило
падением commit_yes на первом же реальном использовании (см.
test_commit_apply_flow.py), потому что почти каждое нажатие кнопки в
боте открывает СВОЮ сессию — job, созданный в одной сессии, почти всегда
читается заново в другой.

Фикс — sqlalchemy.Enum(..., values_callable=...) вместо String(N) (см.
app.db.models._enum_type). Эти тесты фиксируют инвариант на будущее:
enum-поле после свежей загрузки должно быть реальным enum-объектом, а не
строкой, для каждой enum-колонки в проекте."""

from __future__ import annotations

from app.db.models import (
    Finding,
    FindingStatus,
    HistoryEntry,
    Job,
    JobStatus,
    Project,
    ProviderAccount,
    ProviderAccountStatus,
    ProviderMode,
    ProviderName,
    QuotaUsageLog,
    Severity,
    TaskType,
)
from app.db.session import get_session


def _make_project(session) -> int:
    project = Project(name="P", repo_full_name="owner/p")
    session.add(project)
    session.flush()
    return project.id


def test_job_enum_columns_survive_fresh_session(db):
    with get_session() as session:
        project_id = _make_project(session)
        job = Job(
            task_type=TaskType.FIX,
            provider=ProviderName.CLAUDE,
            provider_mode=ProviderMode.AUTO,
            status=JobStatus.RUNNING,
            progress_total=1,
        )
        job.projects = [session.get(Project, project_id)]
        session.add(job)
        session.commit()
        job_id = job.id

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.task_type is TaskType.FIX
        assert job.provider is ProviderName.CLAUDE
        assert job.provider_mode is ProviderMode.AUTO
        assert job.status is JobStatus.RUNNING
        # именно то, что раньше падало с AttributeError на голой строке
        assert job.task_type.value == "fix"


def test_finding_enum_columns_survive_fresh_session(db):
    with get_session() as session:
        project_id = _make_project(session)
        finding = Finding(
            project_id=project_id,
            status=FindingStatus.OPEN,
            severity=Severity.CRITICAL,
            file_symbol="a.py::f",
            description="d",
        )
        session.add(finding)
        session.commit()
        finding_id = finding.id

    with get_session() as session:
        finding = session.get(Finding, finding_id)
        assert finding.status is FindingStatus.OPEN
        assert finding.severity is Severity.CRITICAL


def test_history_entry_enum_columns_survive_fresh_session(db):
    with get_session() as session:
        project_id = _make_project(session)
        entry = HistoryEntry(
            project_id=project_id,
            task_type=TaskType.CHECK_FULL,
            provider=ProviderName.CODEX,
            provider_mode=ProviderMode.MANUAL,
        )
        session.add(entry)
        session.commit()
        entry_id = entry.id

    with get_session() as session:
        entry = session.get(HistoryEntry, entry_id)
        assert entry.task_type is TaskType.CHECK_FULL
        assert entry.provider is ProviderName.CODEX
        assert entry.provider_mode is ProviderMode.MANUAL


def test_provider_account_enum_columns_survive_fresh_session(db):
    with get_session() as session:
        account = ProviderAccount(provider=ProviderName.CURSOR, status=ProviderAccountStatus.CONNECTED)
        session.add(account)
        session.commit()
        account_id = account.id

    with get_session() as session:
        account = session.get(ProviderAccount, account_id)
        assert account.provider is ProviderName.CURSOR
        assert account.status is ProviderAccountStatus.CONNECTED


def test_quota_usage_log_enum_column_survives_fresh_session(db):
    with get_session() as session:
        log = QuotaUsageLog(provider=ProviderName.LOCAL_LLM, input_tokens=1, output_tokens=1)
        session.add(log)
        session.commit()
        log_id = log.id

    with get_session() as session:
        log = session.get(QuotaUsageLog, log_id)
        assert log.provider is ProviderName.LOCAL_LLM
