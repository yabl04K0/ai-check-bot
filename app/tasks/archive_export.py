from __future__ import annotations

from app.db.models import Job, Project
from app.tasks.types import TASK_TYPE_LABELS


def build_handoff_markdown(job: Job, projects: list[Project]) -> str:
    label = TASK_TYPE_LABELS.get(job.task_type, str(job.task_type))
    project_names = ", ".join(p.name for p in projects) or "(без проекта)"
    lines = [
        f"# Хендовер — задача #{job.id} ({label})",
        "",
        f"Проекты: {project_names}",
        f"Статус на момент архивации: {job.status.value}",
        f"Прогресс: шаг {job.progress_step}/{job.progress_total}"
        + (f" — {job.progress_label}" if job.progress_label else ""),
        "",
    ]
    if job.comment:
        lines += ["## Исходная задача", job.comment, ""]
    if job.live_notes:
        lines += ["## Комментарии во время выполнения", job.live_notes, ""]
    if job.progress_detail:
        lines += ["## Последнее, что делал ИИ", job.progress_detail, ""]
    if job.report_text:
        lines += ["## Отчёт/план на текущий момент", job.report_text, ""]
    if job.patch_text:
        lines += ["## Патч (unified diff), если успел сгенерировать", "```diff", job.patch_text, "```", ""]
    if job.handover_note:
        lines += ["## Заметка HANDOVER", job.handover_note, ""]
    lines += [
        "---",
        "Это выгрузка состояния задачи из ai-check-bot — вставь как есть в новый чат с "
        "любой другой ИИ, чтобы продолжить работу оттуда.",
    ]
    return "\n".join(lines)
