"""Business logic for accounts and their probe schedules. Bot handlers and the scheduler
both call through here — neither touches the ORM or a provider SDK directly."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ai_check_bot.config import MAX_PROBES_PER_DAY
from ai_check_bot.models import AIAccount, ProbeRun, ProbeSchedule
from ai_check_bot.providers.registry import PROVIDER_REGISTRY, get_provider

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
# socks5(h)://, http(s):// or a bare host:port — validated loosely, the provider's own
# HTTP client is what actually proves a proxy works or not (a probe run surfaces that).
_PROXY_RE = re.compile(r"^(socks5h?|https?)://\S+$")


class ScheduleLimitError(Exception):
    pass


class InvalidTimeError(Exception):
    pass


class InvalidProxyError(Exception):
    pass


class UnknownProviderError(Exception):
    pass


def add_account(
    session: Session, *, provider: str, label: str, api_key: str, proxy_url: str | None = None
) -> AIAccount:
    # Reject an unsupported provider HERE, not at probe time — otherwise the account
    # saves fine and every future probe (scheduled or manual) crashes with a bare
    # ValueError from get_provider() instead of a clear message at the point of the typo.
    if provider not in PROVIDER_REGISTRY:
        raise UnknownProviderError(f"unknown provider '{provider}', known: {sorted(PROVIDER_REGISTRY)}")
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


def set_account_proxy(session: Session, account: AIAccount, proxy_url: str | None) -> AIAccount:
    """proxy_url=None clears it (account goes back to direct egress)."""
    if proxy_url is not None and not _PROXY_RE.match(proxy_url):
        raise InvalidProxyError(f"'{proxy_url}' does not look like socks5://host:port or http(s)://host:port")
    account.proxy_url = proxy_url
    session.add(account)
    session.commit()
    return account


def set_account_enabled(session: Session, account: AIAccount, enabled: bool) -> AIAccount:
    account.enabled = enabled
    session.add(account)
    session.commit()
    return account


def delete_account(session: Session, account: AIAccount) -> None:
    session.delete(account)  # cascades to schedules/runs, see models.py relationship config
    session.commit()


def get_account_by_label(session: Session, label: str) -> AIAccount | None:
    return session.query(AIAccount).filter_by(label=label).one_or_none()


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
