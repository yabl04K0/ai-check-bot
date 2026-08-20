from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai_check_bot.models import AIAccount, ProbeSchedule
from ai_check_bot.scheduler import sync_jobs


def _add_schedule(session_factory, account_id, time_of_day, enabled=True):
    with session_factory() as session:
        session.add(ProbeSchedule(account_id=account_id, time_of_day=time_of_day, enabled=enabled))
        session.commit()


def test_sync_jobs_creates_one_job_per_enabled_schedule(session_factory, account):
    _add_schedule(session_factory, account.id, "09:00")
    _add_schedule(session_factory, account.id, "18:30")
    _add_schedule(session_factory, account.id, "12:00", enabled=False)

    scheduler = AsyncIOScheduler(timezone="UTC")
    sync_jobs(scheduler, session_factory)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert len(job_ids) == 2
    assert all(j.startswith(f"probe:{account.id}:") for j in job_ids)


def test_sync_jobs_removes_stale_jobs_on_resync(session_factory, account):
    _add_schedule(session_factory, account.id, "09:00")
    scheduler = AsyncIOScheduler(timezone="UTC")
    sync_jobs(scheduler, session_factory)
    assert len(scheduler.get_jobs()) == 1

    with session_factory() as session:
        session.query(ProbeSchedule).delete()
        session.commit()

    sync_jobs(scheduler, session_factory)
    assert len(scheduler.get_jobs()) == 0
