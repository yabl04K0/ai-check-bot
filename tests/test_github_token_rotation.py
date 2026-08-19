from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import GithubTokenState
from app.db.session import get_session
from app.github_integration.rotation import ROTATION_WARNING_DAYS, check_token_age


def test_first_seen_creates_row_with_zero_days(db):
    with get_session() as session:
        age = check_token_age(session, "ghp_test_token")
        session.commit()

    assert age.days_since == 0
    assert age.needs_rotation_warning is False


def test_same_token_reuses_existing_row(db):
    with get_session() as session:
        check_token_age(session, "ghp_same")
        session.commit()

    with get_session() as session:
        rows_before = session.query(GithubTokenState).count()
        check_token_age(session, "ghp_same")
        session.commit()
        rows_after = session.query(GithubTokenState).count()

    assert rows_before == rows_after == 1


def test_different_token_gets_its_own_row(db):
    with get_session() as session:
        check_token_age(session, "ghp_one")
        check_token_age(session, "ghp_two")
        session.commit()

        count = session.query(GithubTokenState).count()

    assert count == 2


def test_old_token_triggers_rotation_warning(db):
    old_date = datetime.now(timezone.utc) - timedelta(days=ROTATION_WARNING_DAYS + 5)
    with get_session() as session:
        # заводим запись, потом откатываем first_seen_at назад — эмулируем
        # токен, который бот "увидел" давно
        check_token_age(session, "ghp_old")
        session.commit()

        row = session.get(GithubTokenState, _hash_of("ghp_old"))
        row.first_seen_at = old_date
        session.commit()

        age = check_token_age(session, "ghp_old")

    assert age.days_since >= ROTATION_WARNING_DAYS
    assert age.needs_rotation_warning is True


def _hash_of(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def test_fresh_token_does_not_warn(db):
    with get_session() as session:
        age = check_token_age(session, "ghp_fresh")
        session.commit()

    assert age.needs_rotation_warning is False
