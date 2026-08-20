"""Picks WHICH account to use when a provider has more than one — the "seamless
multi-account" ask. No real per-account quota telemetry exists yet (README notes
Anthropic/OpenAI/Cursor do not expose one), so the heuristic is least-recently-used by
successful probe/run: an account nobody has touched in a while is preferred, spreading
load across the pool instead of hammering one account until it errors."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ai_check_bot.models import AIAccount, ProbeRun

_EPOCH = dt.datetime.min  # naive, matches models.utc_now()'s storage convention — see its docstring


def _last_used_at(session: Session, account_id: int) -> dt.datetime:
    row = (
        session.query(ProbeRun.ran_at)
        .filter_by(account_id=account_id)
        .order_by(ProbeRun.ran_at.desc())
        .first()
    )
    return row[0] if row is not None else _EPOCH


def pick_account(session: Session, provider: str) -> AIAccount | None:
    """Return the least-recently-used enabled account for `provider`, or None if the
    pool is empty. Callers still get a specific AIAccount back — routing is transparent
    to whatever calls run_probe/get_provider next."""
    accounts = session.query(AIAccount).filter_by(provider=provider, enabled=True).all()
    if not accounts:
        return None
    return min(accounts, key=lambda acc: _last_used_at(session, acc.id))


def pool_size(session: Session, provider: str) -> int:
    return session.query(AIAccount).filter_by(provider=provider, enabled=True).count()
