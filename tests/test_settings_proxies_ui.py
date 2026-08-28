"""⚙️ Настройки → 🌐 Прокси — экран пула + ручной импорт из MeCelium
(см. app/bot/handlers/settings_admin.py::show_proxies/import_proxies_action)."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import settings_admin as settings_module
from app.db.models import ProviderName, ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.providers.gemini import GeminiProvider
from app.providers.registry import ProviderRegistry
from app.proxies.pool import Consumer, assign_proxy


def _run(coro):
    return asyncio.run(coro)


def _callback_update(data: str):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    return SimpleNamespace(callback_query=query), edit


def _context(mecelium_db_path=None):
    settings = SimpleNamespace(admin_tg_id=1, mecelium_db_path=mecelium_db_path)
    registry = ProviderRegistry({ProviderName.GEMINI: GeminiProvider("api-key")})
    bot_data = {"settings": settings, "provider_registry": registry}
    return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))


def test_show_proxies_reports_pool_counts(db):
    with get_session() as session:
        session.add(ProxyPoolEntry(host="1.1.1.1", port=1080, protocol=ProxyProtocol.SOCKS5, import_score=10))
        session.add(ProxyPoolEntry(host="2.2.2.2", port=1080, protocol=ProxyProtocol.SOCKS5, import_score=20))
        session.flush()
        assign_proxy(session, Consumer(provider=ProviderName.GEMINI, account_label="primary"))

    update, edit = _callback_update("set:proxies")
    _run(settings_module.show_proxies(update, _context()))

    args, kwargs = edit.await_args
    assert "Активных: 2" in args[0]
    assert "занято 1" in args[0]
    assert "свободно 1" in args[0]


def test_import_proxies_action_reports_missing_mecelium_path(db):
    update, edit = _callback_update("set:proxies:import")
    _run(settings_module.import_proxies_action(update, _context(mecelium_db_path=None)))

    args, kwargs = edit.await_args
    assert "MECELIUM_DB_PATH" in args[0]


def test_import_proxies_action_imports_and_assigns(tmp_path, db):
    mecelium_db = tmp_path / "mecelium.db"
    con = sqlite3.connect(str(mecelium_db))
    con.execute(
        "CREATE TABLE proxies (id INTEGER PRIMARY KEY, ip TEXT, port INTEGER, protocol TEXT, "
        "status TEXT, reliability REAL, speed_kbps REAL, latency_ms INTEGER)"
    )
    con.execute(
        "INSERT INTO proxies (ip, port, protocol, status, reliability, speed_kbps, latency_ms) "
        "VALUES ('5.5.5.5', 1080, 'SOCKS5', 'VALID', 80.0, 5000.0, 40)"
    )
    con.commit()
    con.close()

    update, edit = _callback_update("set:proxies:import")
    _run(settings_module.import_proxies_action(update, _context(mecelium_db_path=mecelium_db)))

    args, kwargs = edit.await_args
    assert "✅" in args[0]
    assert "1" in args[0]
    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 1
        from app.db.models import ProxyAssignment

        assert session.query(ProxyAssignment).count() == 1  # авто-назначено подключённому Gemini


def test_import_proxies_action_shows_info_icon_when_nothing_imported(tmp_path, db):
    """Раньше показывал ✅ даже когда MeCelium не вернул ни одного нового
    прокси — зелёная галочка на пустом результате (см. аудит меню)."""
    mecelium_db = tmp_path / "mecelium_empty.db"
    con = sqlite3.connect(str(mecelium_db))
    con.execute(
        "CREATE TABLE proxies (id INTEGER PRIMARY KEY, ip TEXT, port INTEGER, protocol TEXT, "
        "status TEXT, reliability REAL, speed_kbps REAL, latency_ms INTEGER)"
    )
    con.commit()
    con.close()

    update, edit = _callback_update("set:proxies:import")
    _run(settings_module.import_proxies_action(update, _context(mecelium_db_path=mecelium_db)))

    args, kwargs = edit.await_args
    assert "ℹ️" in args[0]
    assert "✅" not in args[0]


def test_show_proxies_paginates_beyond_page_size(db):
    with get_session() as session:
        for i in range(25):
            session.add(ProxyPoolEntry(host=f"1.1.1.{i}", port=1080, protocol=ProxyProtocol.SOCKS5))

    update, edit = _callback_update("set:proxies")
    _run(settings_module.show_proxies(update, _context()))

    args, kwargs = edit.await_args
    assert "стр. 1/2" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "set:proxies:page:1" in callbacks


def test_show_proxies_second_page(db):
    with get_session() as session:
        for i in range(25):
            session.add(ProxyPoolEntry(host=f"1.1.1.{i}", port=1080, protocol=ProxyProtocol.SOCKS5))

    update, edit = _callback_update("set:proxies:page:1")
    _run(settings_module.show_proxies_page(update, _context()))

    args, kwargs = edit.await_args
    assert "стр. 2/2" in args[0]
