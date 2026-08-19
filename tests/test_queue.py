from __future__ import annotations

import pytest

from app.db.models import JobStatus, Project, TaskType
from app.db.session import get_session
from app.tasks.queue import JobQueue


def _make_project(session, name="P1") -> int:
    project = Project(name=name, repo_full_name=f"owner/{name.lower()}")
    session.add(project)
    session.flush()
    return project.id


def test_enqueue_and_position(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job1 = queue.enqueue(TaskType.CHECK_FULL, [p1])
        job2 = queue.enqueue(TaskType.CHECK_LITE, [p1])

        assert queue.position_in_queue(job1.id) == 1
        assert queue.position_in_queue(job2.id) == 2
        assert queue.is_busy() is False


def test_next_queued_respects_busy(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job1 = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.enqueue(TaskType.CHECK_LITE, [p1])

        queue.mark_running(job1)
        session.commit()

        assert queue.is_busy() is True
        assert queue.next_queued() is None


def test_pause_and_resume_handover(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(job)
        queue.mark_paused_quota(job, "обрыв на шаге 5/13")
        session.commit()
        job_id = job.id

    with get_session() as session:
        from app.db.models import Job

        job = session.get(Job, job_id)
        assert job.status == JobStatus.PAUSED_QUOTA
        assert "5/13" in job.handover_note

        queue = JobQueue(session)
        resumed = queue.resume_paused()
        assert len(resumed) == 1
        assert resumed[0].status == JobStatus.QUEUED


def test_enqueue_missing_project_raises(db):
    with get_session() as session:
        queue = JobQueue(session)
        with pytest.raises(ValueError):
            queue.enqueue(TaskType.CHECK_FULL, [999])
