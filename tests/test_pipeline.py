from __future__ import annotations

import pytest

from app.db.models import Job, JobStatus, Project, TaskType
from app.db.session import get_session
from app.providers.base import ProviderQuotaExceededError
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
