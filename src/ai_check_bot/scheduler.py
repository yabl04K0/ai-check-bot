"""Wires enabled ProbeSchedule rows onto an APScheduler instance. One cron trigger per
schedule row, keyed so re-running setup() replaces stale jobs instead of duplicating them."""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import sessionmaker

from ai_check_bot.models import AIAccount, ProbeSchedule
from ai_check_bot.probe_service import run_probe


def _job_id(schedule: ProbeSchedule) -> str:
    return f"probe:{schedule.account_id}:{schedule.id}"


async def _run_scheduled_probe(session_factory: sessionmaker, schedule_id: int) -> None:
    with session_factory() as session:
        schedule = session.get(ProbeSchedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        account = session.get(AIAccount, schedule.account_id)
        if account is None:
            return
        await run_probe(session, account, schedule.message)


def sync_jobs(scheduler: AsyncIOScheduler, session_factory: sessionmaker) -> None:
    """Remove every existing probe:* job and re-add one per currently-enabled schedule.
    Call this at startup and after any add/edit/delete of a ProbeSchedule row."""
    for job in scheduler.get_jobs():
        if job.id.startswith("probe:"):
            scheduler.remove_job(job.id)

    with session_factory() as session:
        schedules = session.query(ProbeSchedule).filter_by(enabled=True).all()
        for schedule in schedules:
            hour, minute = schedule.time_of_day.split(":")
            scheduler.add_job(
                _run_scheduled_probe,
                trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
                id=_job_id(schedule),
                args=[session_factory, schedule.id],
                replace_existing=True,
            )


def build_scheduler(session_factory: sessionmaker) -> AsyncIOScheduler:
    # ProbeSchedule.time_of_day is documented and reported to the user as UTC — the
    # scheduler's own timezone must match, or a non-UTC host silently fires probes at
    # the wrong wall-clock time relative to what was configured.
    scheduler = AsyncIOScheduler(timezone="UTC")
    sync_jobs(scheduler, session_factory)
    return scheduler
