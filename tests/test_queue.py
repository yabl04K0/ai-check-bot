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


def test_manual_pause_blocks_queue_like_running(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job1 = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.enqueue(TaskType.CHECK_LITE, [p1])

        queue.mark_running(job1)
        queue.mark_paused_manual(job1)
        session.commit()

        assert job1.status == JobStatus.PAUSED_MANUAL
        assert queue.is_busy() is True  # пауза не освобождает слот
        assert queue.next_queued() is None


def test_mark_resumed_goes_back_to_running(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(job)
        queue.mark_paused_manual(job)
        session.commit()

        queue.mark_resumed(job)
        session.commit()

        assert job.status == JobStatus.RUNNING


def test_reconcile_orphaned_marks_running_and_paused_manual_as_error(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        running = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(running)
        paused_manual = queue.enqueue(TaskType.CHECK_LITE, [p1])
        queue.mark_running(paused_manual)
        queue.mark_paused_manual(paused_manual)
        session.commit()

        orphaned = queue.reconcile_orphaned()
        session.commit()

        assert {j.id for j in orphaned} == {running.id, paused_manual.id}
        assert running.status == JobStatus.ERROR
        assert paused_manual.status == JobStatus.ERROR
        assert "перезапуском" in running.handover_note


def test_reconcile_orphaned_leaves_paused_quota_alone(db):
    """PAUSED_QUOTA законно переживает рестарт — его подхватит
    scheduler._resume_tick, не reconcile_orphaned."""
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(job)
        queue.mark_paused_quota(job, "квота кончилась")
        session.commit()

        orphaned = queue.reconcile_orphaned()

        assert orphaned == []
        assert job.status == JobStatus.PAUSED_QUOTA


def test_reconcile_orphaned_unblocks_queue_for_new_jobs(db):
    """Ключевой сценарий: без reconcile is_busy() навечно видит зависшую
    RUNNING-задачу и блокирует всё остальное после рестарта бота."""
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        stuck = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(stuck)
        session.commit()

        assert queue.is_busy() is True  # симулируем состояние "после падения бота"

        queue.reconcile_orphaned()
        session.commit()

        assert queue.is_busy() is False
        new_job = queue.enqueue(TaskType.CHECK_LITE, [p1])
        assert queue.next_queued().id == new_job.id


def test_add_live_note_appends_timestamped_line(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])

        queue.add_live_note(job, "первая заметка")
        session.commit()

        assert job.live_notes is not None
        assert job.live_notes.startswith("[")
        assert job.live_notes.endswith("первая заметка")


def test_add_live_note_appends_to_existing_notes_with_newline(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])

        queue.add_live_note(job, "первая")
        queue.add_live_note(job, "вторая")
        session.commit()

        lines = job.live_notes.split("\n")
        assert len(lines) == 2
        assert lines[0].endswith("первая")
        assert lines[1].endswith("вторая")


def test_add_live_note_first_call_has_no_leading_newline(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])

        queue.add_live_note(job, "единственная")

        assert "\n" not in job.live_notes


def test_is_busy_true_for_paused_question(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(job)
        job.status = JobStatus.PAUSED_QUESTION
        session.commit()

        assert queue.is_busy() is True
        assert queue.next_queued() is None


def test_reconcile_orphaned_marks_paused_question_as_error(db):
    with get_session() as session:
        p1 = _make_project(session, "P1")
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1])
        queue.mark_running(job)
        job.status = JobStatus.PAUSED_QUESTION
        session.commit()

        orphaned = queue.reconcile_orphaned()
        session.commit()

        assert {j.id for j in orphaned} == {job.id}
        assert job.status == JobStatus.ERROR
        assert "перезапуском" in job.handover_note
