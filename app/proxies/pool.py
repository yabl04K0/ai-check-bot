"""Назначение прокси на потребителя (provider+account_label) — один
прокси на потребителя, без повторов между потребителями: proxy_id
уникален в ProxyAssignment (см. app.db.models), поэтому один и тот же
прокси физически не может достаться двум аккаунтам/API одновременно.
"Потребитель" — пара (provider, account_label), где account_label —
"primary"/"extra:N" (см. app.providers.multi_account.label_credentials)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProviderName, ProxyAssignment, ProxyPoolEntry, ProxyPoolStatus
from app.db.session import get_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Consumer:
    provider: ProviderName
    account_label: str


def _free_proxies(session: Session) -> list[ProxyPoolEntry]:
    assigned_ids = set(session.scalars(select(ProxyAssignment.proxy_id)).all())
    active = session.scalars(
        select(ProxyPoolEntry)
        .where(ProxyPoolEntry.status == ProxyPoolStatus.ACTIVE)
        .order_by(ProxyPoolEntry.import_score.desc())
    ).all()
    return [p for p in active if p.id not in assigned_ids]


def get_assignment(session: Session, consumer: Consumer) -> ProxyAssignment | None:
    return session.scalar(
        select(ProxyAssignment).where(
            ProxyAssignment.provider == consumer.provider,
            ProxyAssignment.account_label == consumer.account_label,
        )
    )


def assign_proxy(session: Session, consumer: Consumer) -> ProxyAssignment | None:
    """Назначает свободный прокси потребителю, если у него ещё нет
    назначения (идемпотентно — повторный вызов просто возвращает то же
    самое). None, если свободных прокси не осталось — вызывающий код (см.
    app.scheduler.proxy_maintenance) решает, что делать с нехваткой."""
    existing = get_assignment(session, consumer)
    if existing is not None:
        return existing
    free = _free_proxies(session)
    if not free:
        return None
    row = ProxyAssignment(
        proxy_id=free[0].id, provider=consumer.provider, account_label=consumer.account_label
    )
    session.add(row)
    session.flush()
    return row


def release_assignment(session: Session, consumer: Consumer) -> None:
    existing = get_assignment(session, consumer)
    if existing is not None:
        session.delete(existing)


def replace_dead_proxy(session: Session, assignment: ProxyAssignment) -> ProxyAssignment | None:
    """Снимает мёртвый прокси с потребителя и пробует назначить свежий из
    оставшегося пула ("если какой-то упадёт — пусть бот его заменит").
    None, если заменить нечем — потребитель временно остаётся без прокси.

    Помечает сам прокси DEAD перед удалением назначения — иначе он тут же
    попал бы обратно в список свободных (assign_proxy видит только занятость
    по ProxyAssignment, не статус) и мог бы "заменить сам себя"."""
    consumer = Consumer(provider=assignment.provider, account_label=assignment.account_label)
    assignment.proxy.status = ProxyPoolStatus.DEAD
    session.delete(assignment)
    session.flush()
    return assign_proxy(session, consumer)


def resolve_proxy_url(session: Session, provider: ProviderName, account_label: str) -> str | None:
    """То, что реально идёт в httpx(proxy=...) — см. app.providers.openai_compatible.
    None — либо нет назначения, либо назначенный прокси уже помечен
    мёртвым (health-check ещё не успел его заменить)."""
    assignment = get_assignment(session, Consumer(provider=provider, account_label=account_label))
    if assignment is None or assignment.proxy.status != ProxyPoolStatus.ACTIVE:
        return None
    return assignment.proxy.url()


def resolve_proxy_url_safe(provider: ProviderName, account_label: str) -> str | None:
    """Как resolve_proxy_url, но открывает свою сессию и никогда не роняет
    вызывающий код — назначение прокси НИКОГДА не должно быть причиной
    падения реального вызова ИИ-провайдера (см. app.providers.openai_compatible._run_once).
    БД не инициализирована / прокси не назначен / что угодно ещё — просто
    None, работаем без прокси."""
    try:
        with get_session() as session:
            return resolve_proxy_url(session, provider, account_label)
    except Exception:  # noqa: BLE001 — намеренно широкий catch, см. докстринг
        logger.warning("Не удалось получить прокси для %s:%s", provider.value, account_label, exc_info=True)
        return None
