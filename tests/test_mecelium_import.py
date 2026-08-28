"""Импорт прокси из MeCelium — читаем чужую БД (read-only), берём только
forward-proxy протоколы (не VPN-туннели), ранжируем по той же формуле
health score, что в MeCelium, апсертим в свой пул без дублей."""

from __future__ import annotations

import sqlite3

import pytest

from app.db.models import ProxyPoolEntry, ProxyProtocol
from app.proxies.mecelium_import import (
    MeCeliumUnavailableError,
    fetch_best_proxies,
    import_top_proxies,
)


def _make_mecelium_db(path, rows):
    """rows: list of (ip, port, protocol, status, reliability, speed_kbps, latency_ms) —
    protocol/status как ИМЯ enum (UPPERCASE), как реально хранит MeCelium."""
    con = sqlite3.connect(str(path))
    con.execute("DROP TABLE IF EXISTS proxies")
    con.execute(
        """
        CREATE TABLE proxies (
            id INTEGER PRIMARY KEY,
            ip TEXT, port INTEGER, protocol TEXT, status TEXT,
            reliability REAL, speed_kbps REAL, latency_ms INTEGER
        )
        """
    )
    con.executemany(
        "INSERT INTO proxies (ip, port, protocol, status, reliability, speed_kbps, latency_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def test_fetch_best_proxies_missing_db_raises(tmp_path):
    with pytest.raises(MeCeliumUnavailableError):
        fetch_best_proxies(tmp_path / "does_not_exist.db")


def test_fetch_best_proxies_orders_by_health_score(tmp_path):
    db = tmp_path / "mecelium.db"
    _make_mecelium_db(
        db,
        [
            ("1.1.1.1", 1080, "SOCKS5", "VALID", 90.0, 5000.0, 50),  # высокий score
            ("2.2.2.2", 1080, "SOCKS5", "VALID", 10.0, 100.0, 400),  # низкий score
        ],
    )
    result = fetch_best_proxies(db, limit=10)
    assert [p.host for p in result] == ["1.1.1.1", "2.2.2.2"]


def test_fetch_best_proxies_excludes_vpn_tunnel_protocols(tmp_path):
    db = tmp_path / "mecelium.db"
    _make_mecelium_db(
        db,
        [
            ("3.3.3.3", 443, "VLESS", "VALID", 99.0, 9999.0, 10),
            ("4.4.4.4", 8080, "HTTP", "VALID", 50.0, 1000.0, 100),
        ],
    )
    result = fetch_best_proxies(db, limit=10)
    assert [p.host for p in result] == ["4.4.4.4"]
    assert result[0].protocol == ProxyProtocol.HTTP


def test_fetch_best_proxies_excludes_non_valid_status(tmp_path):
    db = tmp_path / "mecelium.db"
    _make_mecelium_db(
        db,
        [
            ("5.5.5.5", 1080, "SOCKS5", "PENDING", 99.0, 9999.0, 10),
            ("6.6.6.6", 1080, "SOCKS5", "INVALID", 99.0, 9999.0, 10),
        ],
    )
    result = fetch_best_proxies(db, limit=10)
    assert result == []


def test_fetch_best_proxies_tolerates_null_speed_and_latency(tmp_path):
    """Известный баг измерения скорости на стороне MeCelium — NULL не
    должен рушить запрос или выкидывать прокси из выборки."""
    db = tmp_path / "mecelium.db"
    _make_mecelium_db(db, [("7.7.7.7", 1080, "SOCKS5", "VALID", 20.0, None, None)])
    result = fetch_best_proxies(db, limit=10)
    assert len(result) == 1


def test_fetch_best_proxies_respects_limit(tmp_path):
    db = tmp_path / "mecelium.db"
    rows = [(f"10.0.0.{i}", 1080, "SOCKS5", "VALID", float(i), 1000.0, 100) for i in range(20)]
    _make_mecelium_db(db, rows)
    result = fetch_best_proxies(db, limit=5)
    assert len(result) == 5


def test_import_top_proxies_inserts_into_pool(tmp_path, db):
    mecelium_db = tmp_path / "mecelium.db"
    _make_mecelium_db(mecelium_db, [("8.8.8.8", 1080, "SOCKS5", "VALID", 80.0, 2000.0, 60)])

    from app.db.session import get_session

    with get_session() as session:
        rows = import_top_proxies(session, mecelium_db, limit=10)
        assert len(rows) == 1
        assert rows[0].host == "8.8.8.8"

    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 1


def test_import_top_proxies_upserts_existing_by_endpoint(tmp_path, db):
    mecelium_db = tmp_path / "mecelium.db"
    _make_mecelium_db(mecelium_db, [("9.9.9.9", 1080, "SOCKS5", "VALID", 10.0, 100.0, 300)])

    from app.db.session import get_session

    with get_session() as session:
        import_top_proxies(session, mecelium_db, limit=10)

    _make_mecelium_db(mecelium_db, [("9.9.9.9", 1080, "SOCKS5", "VALID", 99.0, 9000.0, 5)])
    with get_session() as session:
        import_top_proxies(session, mecelium_db, limit=10)

    with get_session() as session:
        rows = session.query(ProxyPoolEntry).all()
        assert len(rows) == 1  # не задублировалось
        assert rows[0].import_score > 50  # обновился новым (высоким) score
