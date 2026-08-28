"""Назначение прокси на аккаунты/API — один прокси не может достаться
двум потребителям одновременно ("чтобы не повторялись"), назначение
идемпотентно, замена мёртвого прокси берёт свободный из оставшегося пула."""

from __future__ import annotations

from app.db.models import ProviderName, ProxyPoolEntry, ProxyPoolStatus, ProxyProtocol
from app.db.session import get_session
from app.proxies.pool import (
    Consumer,
    assign_proxy,
    get_assignment,
    release_assignment,
    replace_dead_proxy,
    resolve_proxy_url,
    resolve_proxy_url_safe,
)


def _add_proxy(session, host, *, score=50.0, status=ProxyPoolStatus.ACTIVE) -> ProxyPoolEntry:
    row = ProxyPoolEntry(
        host=host, port=1080, protocol=ProxyProtocol.SOCKS5, import_score=score, status=status
    )
    session.add(row)
    session.flush()
    return row


def test_assign_proxy_picks_highest_scored_free_proxy(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1", score=10)
        _add_proxy(session, "2.2.2.2", score=90)
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")

        assignment = assign_proxy(session, consumer)

        assert assignment.proxy.host == "2.2.2.2"


def test_assign_proxy_is_idempotent(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        _add_proxy(session, "2.2.2.2")
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")

        first = assign_proxy(session, consumer)
        second = assign_proxy(session, consumer)

        assert first.id == second.id


def test_assign_proxy_never_reuses_an_already_assigned_proxy(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1", score=100)  # единственный прокси
        a = assign_proxy(session, Consumer(provider=ProviderName.GEMINI, account_label="primary"))
        b = assign_proxy(session, Consumer(provider=ProviderName.DEEPSEEK, account_label="primary"))

        assert a is not None
        assert b is None  # свободных больше нет — не повторили тот же прокси


def test_assign_proxy_returns_none_when_pool_empty(db):
    with get_session() as session:
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assert assign_proxy(session, consumer) is None


def test_release_assignment_frees_proxy_for_others(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

        release_assignment(session, consumer)
        session.flush()

        other = Consumer(provider=ProviderName.DEEPSEEK, account_label="primary")
        assert assign_proxy(session, other) is not None


def test_replace_dead_proxy_assigns_a_different_one(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1", score=100)
        _add_proxy(session, "2.2.2.2", score=50)
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assignment = assign_proxy(session, consumer)
        dead_host = assignment.proxy.host

        replacement = replace_dead_proxy(session, assignment)

        assert replacement is not None
        assert replacement.proxy.host != dead_host


def test_replace_dead_proxy_returns_none_when_no_spare(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assignment = assign_proxy(session, consumer)

        replacement = replace_dead_proxy(session, assignment)

        assert replacement is None
        assert get_assignment(session, consumer) is None


def test_resolve_proxy_url_returns_none_for_dead_proxy(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1", status=ProxyPoolStatus.DEAD)
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        # прямое назначение мёртвого прокси через ассигнмент, минуя assign_proxy
        # (assign_proxy сам никогда не выбрал бы мёртвый — а это уже случай
        # "стал мёртвым уже после того, как был назначен")
        from app.db.models import ProxyAssignment

        proxy = session.query(ProxyPoolEntry).one()
        session.add(
            ProxyAssignment(
                proxy_id=proxy.id, provider=consumer.provider, account_label=consumer.account_label
            )
        )
        session.flush()

        assert resolve_proxy_url(session, consumer.provider, consumer.account_label) is None


def test_resolve_proxy_url_returns_url_for_active_assignment(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

        url = resolve_proxy_url(session, consumer.provider, consumer.account_label)
        assert url == "socks5://1.1.1.1:1080"


def test_resolve_proxy_url_safe_never_raises_without_db(monkeypatch):
    """Регрессия: раньше вызов провайдера падал RuntimeError, если БД не
    инициализирована (см. app/providers/openai_compatible.py::_run_once) —
    назначение прокси не должно уметь ронять реальный AI-запрос."""
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "_SessionLocal", None)
    assert resolve_proxy_url_safe(ProviderName.GEMINI, "primary") is None
