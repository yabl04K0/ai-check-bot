"""Очередь задач — SQLite-таблица jobs, как в AutoPost scheduler.

Одновременно выполняется не больше одной задачи (is_busy). Новые задачи
встают в очередь FIFO по created_at. Обрыв по квоте переводит job в
paused_quota (HANDOVER-паттерн) — resume_paused() достаёт их назад, когда
роутер решит, что провайдер снова доступен.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus, Project, ProviderMode, ProviderName, TaskType
from app.tasks.types import STEP_COUNT


class JobQueue:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        task_type: TaskType,
        project_ids: list[int],
        *,
        provider: ProviderName | None = None,
        provider_mode: ProviderMode = ProviderMode.MANUAL,
        scope: str | None = None,
        comment: str | None = None,
        created_by_tg_id: int | None = None,
    ) -> Job:
        projects = self._session.scalars(select(Project).where(Project.id.in_(project_ids))).all()
        if len(projects) != len(set(project_ids)):
            missing = set(project_ids) - {p.id for p in projects}
            raise ValueError(f"Проекты не найдены: {missing}")

        job = Job(
            task_type=task_type,
            provider=provider,
            provider_mode=provider_mode,
            status=JobStatus.QUEUED,
            scope=scope,
            comment=comment,
            progress_step=0,
            progress_total=STEP_COUNT[task_type],
            created_by_tg_id=created_by_tg_id,
        )
        job.projects = list(projects)
        self._session.add(job)
        self._session.flush()
        return job

    def is_busy(self) -> bool:
        """RUNNING занимает воркер; PAUSED_MANUAL тоже — пайплайн жив, просто
        стоит на месте (ждёт ▶️ Продолжить), не отдаёт слот следующей задаче."""
        busy_statuses = (JobStatus.RUNNING, JobStatus.PAUSED_MANUAL)
        return self._session.scalar(select(Job).where(Job.status.in_(busy_statuses))) is not None

    def position_in_queue(self, job_id: int) -> int:
        """1-based позиция среди queued задач, отсортированных по created_at."""
        queued = self._session.scalars(
            select(Job.id).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at)
        ).all()
        try:
            return queued.index(job_id) + 1
        except ValueError:
            return 0

    def next_queued(self) -> Job | None:
        if self.is_busy():
            return None
        return self._session.scalar(
            select(Job).where(Job.status == JobStatus.QUEUED).order_by(Job.created_at).limit(1)
        )

    def mark_running(self, job: Job) -> None:
        from datetime import datetime, timezone

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)

    def update_progress(self, job: Job, step: int, label: str | None = None) -> None:
        job.progress_step = step
        if label is not None:
            job.progress_label = label

    def mark_paused_quota(self, job: Job, handover_note: str) -> None:
        job.status = JobStatus.PAUSED_QUOTA
        job.handover_note = handover_note

    def mark_paused_manual(self, job: Job) -> None:
        job.status = JobStatus.PAUSED_MANUAL

    def mark_resumed(self, job: Job) -> None:
        job.status = JobStatus.RUNNING

    def mark_done(self, job: Job) -> None:
        from datetime import datetime, timezone

        job.status = JobStatus.DONE
        job.finished_at = datetime.now(timezone.utc)

    def mark_cancelled(self, job: Job) -> None:
        from datetime import datetime, timezone

        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(timezone.utc)

    def mark_error(self, job: Job, detail: str) -> None:
        from datetime import datetime, timezone

        job.status = JobStatus.ERROR
        job.handover_note = detail
        job.finished_at = datetime.now(timezone.utc)

    def resume_paused(self) -> list[Job]:
        """Возвращает задачи на паузе по квоте — назад в очередь (см. HANDOVER)."""
        paused = self._session.scalars(
            select(Job).where(Job.status == JobStatus.PAUSED_QUOTA).order_by(Job.created_at)
        ).all()
        for job in paused:
            job.status = JobStatus.QUEUED
        return list(paused)
