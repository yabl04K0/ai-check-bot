from ai_check_bot.models import AIAccount, ProbeRun
from ai_check_bot.providers.router import pick_account, pool_size


def _account(session_factory, label, provider="fake", enabled=True):
    with session_factory() as session:
        acc = AIAccount(provider=provider, label=label, api_key="k", enabled=enabled)
        session.add(acc)
        session.commit()
        session.refresh(acc)
        return acc.id


def _run(session_factory, account_id, success=True):
    with session_factory() as session:
        session.add(ProbeRun(account_id=account_id, success=success))
        session.commit()


def test_pick_account_empty_pool_returns_none(session_factory):
    with session_factory() as session:
        assert pick_account(session, "fake") is None


def test_pick_account_prefers_never_used(session_factory):
    a = _account(session_factory, "a")
    b = _account(session_factory, "b")
    _run(session_factory, a)  # a has a run, b does not

    with session_factory() as session:
        picked = pick_account(session, "fake")
        assert picked.id == b


def test_pick_account_skips_disabled(session_factory):
    _account(session_factory, "a", enabled=False)
    b = _account(session_factory, "b", enabled=True)

    with session_factory() as session:
        picked = pick_account(session, "fake")
        assert picked.id == b


def test_pool_size_counts_only_enabled(session_factory):
    _account(session_factory, "a", enabled=True)
    _account(session_factory, "b", enabled=False)

    with session_factory() as session:
        assert pool_size(session, "fake") == 1
