"""Health-check назначенных прокси + авто-замена — "если какой-то упадёт
то пусть бот его заменит". Мёртвый — FAIL_STREAK_LIMIT неудач подряд, не
одна (единичная сетевая заминка не должна выкидывать рабочий прокси)."""

from __future__ import annotations

from unittest.mock import patch

from app.db.models import ProviderName, ProxyAssignment, ProxyPoolEntry, ProxyPoolStatus, ProxyProtocol
from app.db.session import get_session
from app.proxies import health as health_module
from app.proxies.health import FAIL_STREAK_LIMIT, run_maintenance
from app.proxies.pool import Consumer, assign_proxy


def _add_proxy(session, host, *, score=50.0) -> ProxyPoolEntry:
    row = ProxyPoolEntry(host=host, port=1080, protocol=ProxyProtocol.SOCKS5, import_score=score)
    session.add(row)
    session.flush()
    return row


def test_single_probe_failure_does_not_kill_proxy(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

    with patch.object(health_module, "probe_proxy", return_value=False):
        with get_session() as session:
            result = run_maintenance(session)

    with get_session() as session:
        proxy = session.query(ProxyPoolEntry).one()
        assert proxy.status == ProxyPoolStatus.ACTIVE
        assert proxy.fail_streak == 1
    assert result.replaced == []
    assert result.lost_coverage == []


def test_proxy_marked_dead_and_replaced_after_fail_streak_limit(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1", score=100)
        _add_proxy(session, "2.2.2.2", score=50)  # запасной для замены
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

    with patch.object(health_module, "probe_proxy", return_value=False):
        for _ in range(FAIL_STREAK_LIMIT):
            with get_session() as session:
                result = run_maintenance(session)

    assert result.replaced == [(ProviderName.GEMINI, "primary")]
    with get_session() as session:
        dead = session.query(ProxyPoolEntry).filter_by(host="1.1.1.1").one()
        assert dead.status == ProxyPoolStatus.DEAD
        assignment = session.query(ProxyAssignment).one()
        assert assignment.proxy.host == "2.2.2.2"


def test_reports_lost_coverage_when_no_spare_to_replace_with(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")  # единственный прокси
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

    with patch.object(health_module, "probe_proxy", return_value=False):
        for _ in range(FAIL_STREAK_LIMIT):
            with get_session() as session:
                result = run_maintenance(session)

    assert result.lost_coverage == [(ProviderName.GEMINI, "primary")]
    assert result.replaced == []
    with get_session() as session:
        assert session.query(ProxyAssignment).count() == 0


def test_all_dead_flag_true_when_pool_fully_dead(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

    with patch.object(health_module, "probe_proxy", return_value=False):
        for _ in range(FAIL_STREAK_LIMIT):
            with get_session() as session:
                result = run_maintenance(session)

    assert result.all_dead is True


def test_all_dead_flag_false_when_some_proxy_survives(db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")

    with get_session() as session:
        result = run_maintenance(session)  # никому не назначен — не проверяется, но жив

    assert result.all_dead is False
    assert result.checked == 0


def test_successful_probe_resets_fail_streak(db):
    with get_session() as session:
        proxy = _add_proxy(session, "1.1.1.1")
        proxy.fail_streak = 2
        consumer = Consumer(provider=ProviderName.GEMINI, account_label="primary")
        assign_proxy(session, consumer)

    with patch.object(health_module, "probe_proxy", return_value=True):
        with get_session() as session:
            run_maintenance(session)

    with get_session() as session:
        assert session.query(ProxyPoolEntry).one().fail_streak == 0
