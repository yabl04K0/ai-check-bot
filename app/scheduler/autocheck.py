"""Автопроверка по квоте — см. AUTOGATE в backend-architecture.mermaid.

< 60% недельной квоты осталось → сразу Full ЧЕК на всех авточек-проектах,
высший приоритет. Иначе < 1ч до сброса И < 90% использовано → Lite ЧЕК на
всех. % квоты — своя оценка бота (app.providers.quota), официального API
учёта у провайдеров нет.

Выключено по умолчанию (AUTOCHECK_ENABLED=false); тумблер в рантайме —
application.bot_data["autocheck_enabled_override"] (см. ⚙️ Настройки).
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from telegram.ext import Application

from app.bot.job_runner import start_job
from app.db.models import Job, JobStatus, Project, ProviderMode, TaskType
from app.db.session import get_session
from app.providers.registry import ProviderRegistry
from app.tasks.queue import JobQueue

logger = logging.getLogger(__name__)

TICK_INTERVAL_MINUTES = 15
RESUME_INTERVAL_MINUTES = 5


async def _tick(application: Application) -> None:
    settings = application.bot_data["settings"]
    enabled = application.bot_data.get("autocheck_enabled_override", settings.autocheck.enabled)
    if not enabled:
        return

    registry: ProviderRegistry = application.bot_data["provider_registry"]
    connected = registry.connected()
    if not connected:
        return

    estimates = [registry.get(name).estimate_quota() for name in connected]
    used_values = [e.used_pct for e in estimates if e.used_pct is not None]
    if not used_values:
        return  # ни у одного подключенного провайдера нет оценки — ждём
    worst_used = max(used_values)

    task_type: TaskType | None = None
    if worst_used >= (100 - settings.autocheck.full_threshold_pct):
        task_type = TaskType.CHECK_FULL
    else:
        hours_values = [e.hours_to_reset for e in estimates if e.hours_to_reset is not None]
        soon_reset = any(h < settings.autocheck.lite_hours_before_reset for h in hours_values)
        under_lite_cap = worst_used < settings.autocheck.lite_threshold_pct
        if soon_reset and under_lite_cap:
            task_type = TaskType.CHECK_LITE

    if task_type is None:
        return

    with get_session() as session:
        projects = session.scalars(select(Project).where(Project.autocheck_enabled.is_(True))).all()
        if not projects:
            return
        project_ids = [p.id for p in projects]

        queue = JobQueue(session)
        job = queue.enqueue(
            task_type,
            project_ids,
            provider_mode=ProviderMode.AUTO,
            scope="all",
            comment="Автопроверка по квоте (см. Настройки → Авточек)",
            created_by_tg_id=settings.admin_tg_id,
        )
        job_id = job.id
        should_start = not queue.is_busy() and queue.position_in_queue(job_id) == 1

    logger.info("Автопроверка: поставлена задача #%s (%s)", job_id, task_type.value)
    if should_start:
        asyncio.create_task(start_job(application, job_id))


async def _resume_tick(application: Application) -> None:
    """HANDOVER-паттерн: задачи на паузе по квоте возвращаются в очередь,
    как только оценка квоты провайдера снова выглядит приемлемой (или
    оценки вообще нет — тогда пробуем оптимистично, честной альтернативы
    без официального API квоты нет)."""
    registry: ProviderRegistry = application.bot_data["provider_registry"]

    with get_session() as session:
        paused = session.scalars(select(Job).where(Job.status == JobStatus.PAUSED_QUOTA)).all()
        resumed_ids = []
        for job in paused:
            if job.provider is not None:
                estimate = registry.get(job.provider).estimate_quota()
                if estimate.used_pct is not None and estimate.used_pct >= 95:
                    continue
            job.status = JobStatus.QUEUED
            resumed_ids.append(job.id)
        session.commit()

        if not resumed_ids:
            return

        queue = JobQueue(session)
        busy = queue.is_busy()
        next_job = queue.next_queued()
        next_job_id = next_job.id if next_job else None

    logger.info("Возобновлены задачи после паузы по квоте: %s", resumed_ids)
    if not busy and next_job_id:
        asyncio.create_task(start_job(application, next_job_id))


def start_scheduler(application: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _tick,
        "interval",
        minutes=TICK_INTERVAL_MINUTES,
        args=[application],
        id="autocheck_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _resume_tick,
        "interval",
        minutes=RESUME_INTERVAL_MINUTES,
        args=[application],
        id="resume_paused_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
