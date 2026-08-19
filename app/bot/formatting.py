"""Форматирование сообщений — прогресс-бар, отчёты."""

from __future__ import annotations

from app.db.models import Job, JobStatus
from app.tasks.types import TASK_TYPE_LABELS


def _bar(step: int, total: int, width: int = 10) -> str:
    if total <= 0:
        filled = 0
    else:
        filled = round(width * min(step, total) / total)
    return "▓" * filled + "░" * (width - filled)


def render_progress(job: Job) -> str:
    label = TASK_TYPE_LABELS.get(job.task_type, job.task_type)
    pct = int(100 * job.progress_step / job.progress_total) if job.progress_total else 0
    bar = _bar(job.progress_step, job.progress_total)
    step_label = job.progress_label or ""
    return (
        f"📊 ПРОГРЕСС — {label}\n"
        f"{bar} {pct}%\n"
        f"Шаг {job.progress_step}/{job.progress_total}: {step_label}"
    )


def render_interrupted(job: Job) -> str:
    return (
        f"⏸ Приостановлено: лимит квоты\n"
        f"{job.handover_note or ''}\n"
        f"Возобновится автоматически после сброса лимита."
    )


def render_error(job: Job) -> str:
    return f"❌ Ошибка выполнения\n{job.handover_note or ''}"


def render_report_header(job: Job, findings_summary: str | None = None) -> str:
    label = TASK_TYPE_LABELS.get(job.task_type, job.task_type)
    header = f"📋 ОТЧЁТ — {label}"
    if findings_summary:
        header += f"\n{findings_summary}"
    return header


def render_job_status_line(job: Job) -> str:
    icons = {
        JobStatus.QUEUED: "⏳",
        JobStatus.RUNNING: "▶️",
        JobStatus.PAUSED_QUOTA: "⏸",
        JobStatus.DONE: "✅",
        JobStatus.CANCELLED: "✖",
        JobStatus.ERROR: "❌",
    }
    label = TASK_TYPE_LABELS.get(job.task_type, job.task_type)
    return f"{icons.get(job.status, '?')} #{job.id} {label} — {job.status.value}"
