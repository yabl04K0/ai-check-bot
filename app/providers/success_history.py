"""История успешности провайдера на задачах этого типа — вход для
роутера "Авто" (см. app.providers.router.pick_provider,
success_scores). Источник — таблица Job (не HistoryEntry: она пишется
только для job.status==DONE, там нет ни одной неудачи, по ней нельзя
посчитать долю успеха)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus, ProviderName, TaskType

TERMINAL_STATUSES = (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED)
DEFAULT_LOOKBACK = 20


def compute_success_scores(
    session: Session, task_type: TaskType, *, lookback: int = DEFAULT_LOOKBACK
) -> dict[ProviderName, float]:
    """Доля job.status==DONE среди последних `lookback` завершённых прогонов
    (DONE/ERROR/CANCELLED — не queued/running/paused, они ещё не про
    результат) на провайдер для этого типа задачи.

    Провайдер без истории на этом типе задачи в словарь не попадает —
    роутер трактует отсутствие данных как "не знаю", не как "плохой"."""
    scores: dict[ProviderName, float] = {}
    for provider_name in ProviderName:
        statuses = session.scalars(
            select(Job.status)
            .where(
                Job.task_type == task_type,
                Job.provider == provider_name,
                Job.status.in_(TERMINAL_STATUSES),
            )
            # id, не created_at: created_at может совпасть с точностью до
            # микросекунды на быстрых вставках подряд, id строго монотонный.
            .order_by(Job.id.desc())
            .limit(lookback)
        ).all()
        if not statuses:
            continue
        successes = sum(1 for status in statuses if status == JobStatus.DONE)
        scores[provider_name] = successes / len(statuses)
    return scores
