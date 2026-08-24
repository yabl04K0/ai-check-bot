"""Форматирование сообщений — прогресс-бар, отчёты."""

from __future__ import annotations

from app.db.models import Job, JobStatus
from app.providers.quota import account_usage_summary
from app.tasks.types import TASK_TYPE_LABELS


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _limits_line(job: Job) -> str:
    """Самооценка расхода токенов текущего провайдера за 5ч/неделю — не %
    от Anthropic (такого API нет), просто то, что бот сам отправил, см.
    app.providers.quota.account_usage_summary."""
    if not job.provider:
        return ""
    summary = account_usage_summary(job.provider)
    if not summary:
        return ""
    parts = [
        f"{label or 'default'}: 5ч {_fmt_tokens(five_h)}/нед {_fmt_tokens(week)}"
        for label, (five_h, week) in sorted(summary.items(), key=lambda kv: kv[0] or "")
    ]
    return f"\n💳 {job.provider.value} — " + ", ".join(parts)


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
    pause_note = "⏸ НА ПАУЗЕ — нажми «▶️ Продолжить»\n" if job.status == JobStatus.PAUSED_MANUAL else ""
    detail_line = f"\n💬 {job.progress_detail}" if job.progress_detail else ""
    return (
        f"{pause_note}"
        f"📊 ПРОГРЕСС — {label}\n"
        f"{bar} {pct}%\n"
        f"Шаг {job.progress_step}/{job.progress_total}: {step_label}"
        f"{detail_line}"
        f"{_limits_line(job)}"
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
        JobStatus.PAUSED_MANUAL: "⏸",
        JobStatus.DONE: "✅",
        JobStatus.CANCELLED: "✖",
        JobStatus.ERROR: "❌",
    }
    label = TASK_TYPE_LABELS.get(job.task_type, job.task_type)
    return f"{icons.get(job.status, '?')} #{job.id} {label} — {job.status.value}"
