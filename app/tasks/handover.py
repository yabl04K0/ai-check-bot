"""HANDOVER — синхронизация LAST_PROMPT.md/STATE_LOG.md/PROJECT_MEMORY.md
в конце прогона job'ы, тот же паттерн, что CLAUDE.md/AI_COMMANDS.md
предписывают ручной AI-сессии на HANDOVER-триггер ("что сделано, текущее
состояние, STOPPING POINT, что дальше, открытые вопросы"). Для бота
"сессия" = один прогон job'ы; вызывается из app.bot.job_runner сразу
после того, как пайплайн дошёл до терминального статуса.

PROJECT_MEMORY.md не создаётся ботом с нуля (см. registry_store.project_memory)
— запись в SESSION LOG добавляется, только если файл уже существует.
STATE_LOG.md, наоборот, бот вправе завести сам — это чистый машинный лог.
"""

from __future__ import annotations

from app.db.models import Job, JobStatus, Project
from app.registry_store.last_prompt import write_last_prompt
from app.registry_store.project_memory import append_session_log_entry
from app.registry_store.state_log import append_entry
from app.tasks.project_context import local_path as project_local_path
from app.tasks.types import TASK_TYPE_LABELS

_STOPPING_POINT = {
    JobStatus.DONE: "job finished normally, report delivered to the user",
    JobStatus.ERROR: "job stopped on error before finishing",
    JobStatus.PAUSED_QUOTA: "job paused — AI provider quota exhausted, will auto-resume when available",
    JobStatus.CANCELLED: "job cancelled by the user before finishing",
}

_NEXT_HINT = {
    JobStatus.DONE: "none — job completed, see report_text for follow-up findings if any",
    JobStatus.ERROR: "investigate the error below and decide whether to retry",
    JobStatus.PAUSED_QUOTA: "nothing to do — scheduler resumes it automatically once quota frees up",
    JobStatus.CANCELLED: "none unless the user re-queues the task",
}


def run_handover(job: Job, projects: list[Project]) -> None:
    label = TASK_TYPE_LABELS.get(job.task_type, str(job.task_type))
    summary = f"ai-check-bot ran {label} (job #{job.id})" + (f": {job.comment}" if job.comment else "")
    stopping_point = _STOPPING_POINT.get(job.status, job.status.value)
    next_note = job.handover_note or _NEXT_HINT.get(job.status, "unknown")

    for project in projects:
        path = project_local_path(project)
        if path is None:
            continue

        append_entry(
            path,
            "HANDOVER",
            {
                "session_summary": summary,
                "stopping_point": stopping_point,
                "next": next_note,
                "open_questions": "none",
            },
        )

        if job.report_text:
            write_last_prompt(
                path,
                f"{label} (job #{job.id}, {job.status.value}) — continue from here:\n\n"
                f"{job.report_text[:2000]}",
            )
            append_session_log_entry(
                path,
                f"ai-check-bot: {label} #{job.id}",
                job.report_text[:4000],
            )
