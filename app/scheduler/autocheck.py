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
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from telegram.ext import Application

from app.bot.job_runner import start_job
from app.db.models import Job, JobStatus, Project, ProviderAccountStatus, ProviderMode, TaskType
from app.db.session import get_session
from app.providers.registry import ProviderRegistry
from app.providers.router import fallback_chain
from app.scheduler.decision import decide_autocheck_action
from app.scheduler.health_monitor import check_and_notify as _health_check_tick
from app.scheduler.quota_warnings import check_and_warn as _quota_warning_tick
from app.tasks.queue import JobQueue

logger = logging.getLogger(__name__)

TICK_INTERVAL_MINUTES = 15
RESUME_INTERVAL_MINUTES = 5
NIGHTLY_TICK_INTERVAL_MINUTES = 5
HEALTH_CHECK_INTERVAL_MINUTES = 30


_PENDING_AUTO_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.PAUSED_MANUAL,
    JobStatus.PAUSED_QUOTA,
)


async def _tick(application: Application) -> None:
    settings = application.bot_data["settings"]
    enabled = application.bot_data.get("autocheck_enabled_override", settings.autocheck.enabled)
    registry: ProviderRegistry = application.bot_data["provider_registry"]

    decision = decide_autocheck_action(settings.autocheck, enabled=enabled, registry=registry)
    if not decision.would_run:
        return
    task_type = decision.task_type

    with get_session() as session:
        already_pending = session.scalar(
            select(Job).where(
                Job.provider_mode == ProviderMode.AUTO,
                Job.status.in_(_PENDING_AUTO_STATUSES),
            )
        )
        if already_pending is not None:
            # Тик каждые 15 мин, условие квоты может держаться часами —
            # без этой проверки очередь копила бы дубликат автопроверки
            # на каждом тике, пока условие не перестанет выполняться.
            return

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


def _now() -> datetime:
    return datetime.now()


def _is_within_nightly_window(time_str: str, now: datetime) -> bool:
    try:
        target = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return False
    target_dt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    elapsed = (now - target_dt).total_seconds()
    return 0 <= elapsed < NIGHTLY_TICK_INTERVAL_MINUTES * 60


async def _nightly_tick(application: Application) -> None:
    settings = application.bot_data["settings"]
    now = _now()
    today = now.date().isoformat()

    with get_session() as session:
        already_pending = session.scalar(
            select(Job).where(
                Job.provider_mode == ProviderMode.AUTO,
                Job.status.in_(_PENDING_AUTO_STATUSES),
            )
        )
        if already_pending is not None:
            return

        projects = session.scalars(select(Project).where(Project.nightly_check_time.is_not(None))).all()
        queue = JobQueue(session)
        started_job_id = None
        enqueued_ids = []
        for project in projects:
            if project.nightly_last_run_date == today:
                continue
            if not _is_within_nightly_window(project.nightly_check_time, now):
                continue

            job = queue.enqueue(
                TaskType.CHECK_FULL,
                [project.id],
                provider_mode=ProviderMode.AUTO,
                scope="all",
                comment="Ночная проверка по расписанию",
                created_by_tg_id=settings.admin_tg_id,
            )
            project.nightly_last_run_date = today
            enqueued_ids.append(job.id)
            if started_job_id is None and not queue.is_busy() and queue.position_in_queue(job.id) == 1:
                started_job_id = job.id

    if not enqueued_ids:
        return

    logger.info("Ночная проверка по расписанию: поставлены задачи %s", enqueued_ids)
    if started_job_id:
        asyncio.create_task(start_job(application, started_job_id))


def _chain_has_available_provider(registry: ProviderRegistry, job: Job) -> bool:
    for name in fallback_chain(job.task_type):
        if registry.is_disabled(name):
            continue
        provider = registry.get(name)
        if provider.auth_status().status != ProviderAccountStatus.CONNECTED:
            continue
        estimate = provider.estimate_quota()
        if estimate.used_pct is None or estimate.used_pct < 95:
            return True
    return False


async def _resume_tick(application: Application) -> None:
    registry: ProviderRegistry = application.bot_data["provider_registry"]

    with get_session() as session:
        paused = session.scalars(select(Job).where(Job.status == JobStatus.PAUSED_QUOTA)).all()
        resumed_ids = []
        for job in paused:
            if not _chain_has_available_provider(registry, job):
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
    scheduler.add_job(
        _quota_warning_tick,
        "interval",
        minutes=TICK_INTERVAL_MINUTES,
        args=[application],
        id="quota_warning_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _nightly_tick,
        "interval",
        minutes=NIGHTLY_TICK_INTERVAL_MINUTES,
        args=[application],
        id="nightly_check_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _health_check_tick,
        "interval",
        minutes=HEALTH_CHECK_INTERVAL_MINUTES,
        args=[application],
        id="health_check_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
