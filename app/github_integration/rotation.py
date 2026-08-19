"""Напоминание о ротации GITHUB_TOKEN раз в 30 дней (см. README, G5 в
backend-architecture.mermaid).

Fine-grained PAT не отдаёт дату выпуска через API, поэтому отсчитываем от
момента, когда бот впервые увидел этот конкретный токен (по хэшу — если
токен в .env поменяли, отсчёт сам начнётся заново). Честная оценка, не
точная дата — как и оценка квоты в app.providers.quota.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import GithubTokenState

ROTATION_WARNING_DAYS = 30


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class TokenAge:
    first_seen_at: datetime
    days_since: int

    @property
    def needs_rotation_warning(self) -> bool:
        return self.days_since >= ROTATION_WARNING_DAYS


def check_token_age(session: Session, token: str) -> TokenAge:
    """Смотрит (и заводит при первом обращении) запись для этого токена."""
    token_hash = _token_hash(token)
    row = session.get(GithubTokenState, token_hash)
    if row is None:
        row = GithubTokenState(token_hash=token_hash)
        session.add(row)
        session.flush()

    first_seen = row.first_seen_at
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    days_since = (datetime.now(timezone.utc) - first_seen).days
    return TokenAge(first_seen_at=first_seen, days_since=days_since)
