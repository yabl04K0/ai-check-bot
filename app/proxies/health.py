"""Health-check назначенных прокси + авто-замена мёртвых ("если какой-то
упадёт то пусть бот его заменит"). Мёртвый — FAIL_STREAK_LIMIT неудачных
проверок ПОДРЯД, не одна: единичная сетевая заминка не должна выкидывать
рабочий прокси из-под живого аккаунта."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ProviderName, ProxyAssignment, ProxyPoolEntry, ProxyPoolStatus
from app.proxies.pool import replace_dead_proxy

FAIL_STREAK_LIMIT = 3
PROBE_URL = "https://api.ipify.org"
PROBE_TIMEOUT = 10.0


def probe_proxy(proxy: ProxyPoolEntry, *, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        with httpx.Client(proxy=proxy.url(), timeout=timeout) as client:
            response = client.get(PROBE_URL)
            return response.status_code < 500
    except httpx.HTTPError:
        return False


@dataclass
class MaintenanceResult:
    checked: int = 0
    replaced: list[tuple[ProviderName, str]] = field(default_factory=list)
    lost_coverage: list[tuple[ProviderName, str]] = field(default_factory=list)
    all_dead: bool = False


def run_maintenance(session: Session) -> MaintenanceResult:
    """Прогоняет health-check по каждому НАЗНАЧЕННОМУ прокси. Не трогает
    свободные (незанятые) прокси в пуле — их незачем гонять до того, как
    они кому-то реально понадобятся."""
    result = MaintenanceResult()
    assignments = session.scalars(select(ProxyAssignment)).all()

    for assignment in assignments:
        proxy = assignment.proxy
        result.checked += 1
        ok = probe_proxy(proxy)
        proxy.last_checked_at = datetime.now(timezone.utc)
        if ok:
            proxy.fail_streak = 0
            continue

        proxy.fail_streak += 1
        if proxy.fail_streak < FAIL_STREAK_LIMIT:
            continue

        proxy.status = ProxyPoolStatus.DEAD
        consumer_key = (assignment.provider, assignment.account_label)
        replacement = replace_dead_proxy(session, assignment)
        if replacement is None:
            result.lost_coverage.append(consumer_key)
        else:
            result.replaced.append(consumer_key)

    active_left = session.scalar(
        select(func.count())
        .select_from(ProxyPoolEntry)
        .where(ProxyPoolEntry.status == ProxyPoolStatus.ACTIVE)
    )
    result.all_dead = active_left == 0
    return result
