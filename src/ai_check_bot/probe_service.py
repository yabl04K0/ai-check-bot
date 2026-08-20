"""Business logic for accounts and their probe schedules. Bot handlers and the scheduler
both call through here — neither touches the ORM or a provider SDK directly."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ai_check_bot.config import MAX_PROBES_PER_DAY
from ai_check_bot.models import AIAccount, ProbeRun, ProbeSchedule
from ai_check_bot.providers.registry import get_provider

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleLimitError(Exception):
    pass


class InvalidTimeError(Exception):
    pass


def add_account(
    session: Session, *, provider: str, label: str, api_key: str, proxy_url: str | None = None
) -> AIAccount:
    account = AIAccount(provider=provider, label=label, api_key=api_key, proxy_url=proxy_url)
    session.add(account)
    session.commit()
    return account


def add_schedule(
    session: Session, *, account: AIAccount, time_of_day: str, message: str = "ping"
) -> ProbeSchedule:
    if not _TIME_RE.match(time_of_day):
        raise InvalidTimeError(f"'{time_of_day}' is not HH:MM (24h)")
    # Query directly rather than trust account.schedules: with expire_on_commit=False the
    # relationship collection caches on first access and does not see schedules added via
    # the account_id column (not the relationship attribute) later in the same session.
    active = (
        session.query(ProbeSchedule)
        .filter_by(account_id=account.id, enabled=True)
        .count()
    )
    if active >= MAX_PROBES_PER_DAY:
        raise ScheduleLimitError(f"account already has {MAX_PROBES_PER_DAY} probes/day, the max")
    schedule = ProbeSchedule(account_id=account.id, time_of_day=time_of_day, message=message)
    session.add(schedule)
    session.commit()
    return schedule


async def run_probe(session: Session, account: AIAccount, message: str) -> ProbeRun:
    provider = get_provider(account)
    result = await provider.probe(message)
    run = ProbeRun(
        account_id=account.id,
        success=result.success,
        latency_ms=result.latency_ms,
        error=result.error,
    )
    session.add(run)
    session.commit()
    return run
