"""Тик обслуживания пула прокси — health-check назначенных + назначение
недостающим потребителям + уведомления владельцу при нехватке/поголовной
смерти пула (см. app/scheduler/proxy_maintenance.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.db.models import ProviderAccountStatus, ProviderName, ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.providers.base import AuthStatus
from app.providers.registry import ProviderRegistry
from app.proxies import health as health_module
from app.scheduler import proxy_maintenance


def _run(coro):
    return asyncio.run(coro)


class _FakeProvider:
    def __init__(self, name: ProviderName) -> None:
        self.name = name

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)


def _application(tmp_path):
    registry = ProviderRegistry({ProviderName.GEMINI: _FakeProvider(ProviderName.GEMINI)})
    send = AsyncMock()
    settings = SimpleNamespace(admin_tg_id=1, db_path=tmp_path / "bot.sqlite3")
    return SimpleNamespace(
        bot_data={"settings": settings, "provider_registry": registry},
        bot=SimpleNamespace(send_message=send),
    ), send


def test_tick_assigns_proxy_to_connected_provider_missing_one(db, tmp_path):
    with get_session() as session:
        session.add(ProxyPoolEntry(host="1.1.1.1", port=1080, protocol=ProxyProtocol.SOCKS5, import_score=10))

    application, send = _application(tmp_path)
    with patch.object(health_module, "probe_proxy", return_value=True):
        _run(proxy_maintenance._tick(application))

    with get_session() as session:
        from app.db.models import ProxyAssignment

        assert session.query(ProxyAssignment).count() == 1
    send.assert_not_awaited()  # покрытие есть — уведомлять не о чем


def test_tick_notifies_owner_when_no_proxies_for_connected_provider(db, tmp_path):
    application, send = _application(tmp_path)  # пул пуст

    _run(proxy_maintenance._tick(application))

    send.assert_awaited_once()
    args, kwargs = send.await_args
    assert "не хватает" in args[1].lower() or "gemini" in args[1].lower()


def test_tick_notifies_owner_when_pool_fully_dead(db, tmp_path):
    with get_session() as session:
        session.add(ProxyPoolEntry(host="1.1.1.1", port=1080, protocol=ProxyProtocol.SOCKS5, import_score=10))

    application, send = _application(tmp_path)
    with patch.object(health_module, "probe_proxy", return_value=True):
        _run(proxy_maintenance._tick(application))  # первый тик — назначает и проверяет (жив)

    send.reset_mock()
    with patch.object(health_module, "probe_proxy", return_value=False):
        for _ in range(health_module.FAIL_STREAK_LIMIT):
            _run(proxy_maintenance._tick(application))

    texts = " ".join(call.args[1] for call in send.await_args_list)
    assert "мертв" in texts.lower() or "мёртв" in texts.lower()
