from __future__ import annotations

from app.db.models import Job, JobStatus, Project, ProviderMode, ProviderName, TaskType
from app.db.session import get_session
from app.providers.success_history import compute_success_scores


def _make_project(session) -> Project:
    project = Project(name="P", repo_full_name="owner/p")
    session.add(project)
    session.flush()
    return project


def _make_job(
    session, project, *, provider: ProviderName, status: JobStatus, task_type=TaskType.CHECK_FULL
) -> Job:
    job = Job(
        task_type=task_type,
        provider=provider,
        provider_mode=ProviderMode.AUTO,
        status=status,
        progress_total=1,
    )
    job.projects = [project]
    session.add(job)
    session.flush()
    return job


def test_no_history_means_no_score(db):
    with get_session() as session:
        scores = compute_success_scores(session, TaskType.CHECK_FULL)
        assert scores == {}


def test_all_done_gives_score_one(db):
    with get_session() as session:
        project = _make_project(session)
        for _ in range(3):
            _make_job(session, project, provider=ProviderName.CLAUDE, status=JobStatus.DONE)
        session.commit()

        scores = compute_success_scores(session, TaskType.CHECK_FULL)
        assert scores[ProviderName.CLAUDE] == 1.0


def test_mixed_outcomes_gives_fraction(db):
    with get_session() as session:
        project = _make_project(session)
        _make_job(session, project, provider=ProviderName.CODEX, status=JobStatus.DONE)
        _make_job(session, project, provider=ProviderName.CODEX, status=JobStatus.ERROR)
        _make_job(session, project, provider=ProviderName.CODEX, status=JobStatus.DONE)
        _make_job(session, project, provider=ProviderName.CODEX, status=JobStatus.CANCELLED)
        session.commit()

        scores = compute_success_scores(session, TaskType.CHECK_FULL)
        assert scores[ProviderName.CODEX] == 0.5  # 2 из 4


def test_queued_and_running_jobs_are_not_counted(db):
    """Незавершённые job'ы (queued/running/paused_*) — ещё не про результат."""
    with get_session() as session:
        project = _make_project(session)
        _make_job(session, project, provider=ProviderName.CLAUDE, status=JobStatus.QUEUED)
        _make_job(session, project, provider=ProviderName.CLAUDE, status=JobStatus.RUNNING)
        _make_job(session, project, provider=ProviderName.CLAUDE, status=JobStatus.PAUSED_QUOTA)
        session.commit()

        scores = compute_success_scores(session, TaskType.CHECK_FULL)
        assert ProviderName.CLAUDE not in scores


def test_task_type_scoped_separately(db):
    with get_session() as session:
        project = _make_project(session)
        _make_job(
            session, project, provider=ProviderName.CLAUDE, status=JobStatus.DONE, task_type=TaskType.FIX
        )
        _make_job(
            session,
            project,
            provider=ProviderName.CLAUDE,
            status=JobStatus.ERROR,
            task_type=TaskType.CHECK_FULL,
        )
        session.commit()

        assert compute_success_scores(session, TaskType.FIX)[ProviderName.CLAUDE] == 1.0
        assert compute_success_scores(session, TaskType.CHECK_FULL)[ProviderName.CLAUDE] == 0.0


def test_lookback_limits_to_recent_jobs(db):
    with get_session() as session:
        project = _make_project(session)
        for _ in range(5):
            _make_job(session, project, provider=ProviderName.CLAUDE, status=JobStatus.ERROR)
        for _ in range(2):
            _make_job(session, project, provider=ProviderName.CLAUDE, status=JobStatus.DONE)
        session.commit()

        # последние 2 (лимит) — оба DONE, хотя всего успехов 2 из 7
        scores = compute_success_scores(session, TaskType.CHECK_FULL, lookback=2)
        assert scores[ProviderName.CLAUDE] == 1.0
