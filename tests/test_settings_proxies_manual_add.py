"""⚙️ Настройки → 🌐 Прокси → ✍️ Добавить вручную — вставленный текстом
список прокси (см. app/bot/handlers/settings_admin.py::prompt_add_proxies_manual/
receive_proxies_manual_text, app/proxies/manual_import.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.bot.handlers import settings_admin as settings_module
from app.db.models import ProviderAccountStatus, ProviderName, ProxyAssignment, ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.providers.base import AuthStatus
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


class _FakeProvider:
    def __init__(self, name: ProviderName) -> None:
        self.name = name

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)


def _bot_data(registry, tmp_path):
    settings = SimpleNamespace(db_path=tmp_path / "bot.sqlite3")
    return {"provider_registry": registry, "settings": settings}


def test_prompt_add_proxies_manual_sets_awaiting(db):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="set:proxies:add")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={})

    _run(settings_module.prompt_add_proxies_manual(update, context))

    assert context.user_data["awaiting"] == "proxies_manual_add"


def test_receive_proxies_manual_text_adds_and_assigns(db, tmp_path):
    reply = AsyncMock()
    message = SimpleNamespace(text="1.1.1.1:1080\n2.2.2.2:8080:http\n", reply_text=reply)
    update = SimpleNamespace(message=message)
    registry = ProviderRegistry({ProviderName.GEMINI: _FakeProvider(ProviderName.GEMINI)})
    context = SimpleNamespace(
        user_data={"awaiting": "proxies_manual_add"},
        application=SimpleNamespace(bot_data=_bot_data(registry, tmp_path)),
    )

    _run(settings_module.receive_proxies_manual_text(update, context))

    assert context.user_data["awaiting"] is None
    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 2
        assert session.query(ProxyAssignment).count() == 1  # единственный подключённый потребитель
    reply.assert_awaited_once()
    args, kwargs = reply.await_args
    assert "2" in args[0]
    assert "✅" in args[0]
    # Раньше не давал ни одной кнопки для возврата — самое свежее
    # сообщение в чате оставалось тупиковым (см. аудит меню).
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "set:proxies" in callbacks


def test_receive_proxies_manual_text_shows_warning_icon_when_nothing_added(db, tmp_path):
    """Раньше показывал ✅ даже когда ни одна строка не распозналась —
    зелёная галочка при полном провале (см. аудит меню)."""
    reply = AsyncMock()
    message = SimpleNamespace(text="garbage line only", reply_text=reply)
    update = SimpleNamespace(message=message)
    registry = ProviderRegistry({})
    context = SimpleNamespace(
        user_data={"awaiting": "proxies_manual_add"},
        application=SimpleNamespace(bot_data=_bot_data(registry, tmp_path)),
    )

    _run(settings_module.receive_proxies_manual_text(update, context))

    args, kwargs = reply.await_args
    assert "⚠️" in args[0]
    assert "✅ Добавлено" not in args[0]


def test_receive_proxies_manual_text_ignores_when_not_awaiting(db, tmp_path):
    reply = AsyncMock()
    message = SimpleNamespace(text="1.1.1.1:1080", reply_text=reply)
    update = SimpleNamespace(message=message)
    bot_data = _bot_data(ProviderRegistry({}), tmp_path)
    context = SimpleNamespace(user_data={"awaiting": None}, application=SimpleNamespace(bot_data=bot_data))

    _run(settings_module.receive_proxies_manual_text(update, context))

    reply.assert_not_awaited()
    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 0


def test_receive_proxies_manual_text_reports_unrecognized_lines(db, tmp_path):
    reply = AsyncMock()
    message = SimpleNamespace(text="1.1.1.1:1080\ngarbage line\n", reply_text=reply)
    update = SimpleNamespace(message=message)
    registry = ProviderRegistry({})
    context = SimpleNamespace(
        user_data={"awaiting": "proxies_manual_add"},
        application=SimpleNamespace(bot_data=_bot_data(registry, tmp_path)),
    )

    _run(settings_module.receive_proxies_manual_text(update, context))

    args, kwargs = reply.await_args
    assert "garbage line" in args[0]


def test_receive_proxies_manual_text_shadowsocks_triggers_bridge_restart(db, tmp_path):
    reply = AsyncMock()
    uri = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpvWklvQTY5UTh5aGNRVjhrYTNQYTNB@82.38.31.26:8080"
    message = SimpleNamespace(text=uri, reply_text=reply)
    update = SimpleNamespace(message=message)
    registry = ProviderRegistry({})
    context = SimpleNamespace(
        user_data={"awaiting": "proxies_manual_add"},
        application=SimpleNamespace(bot_data=_bot_data(registry, tmp_path)),
    )

    with patch.object(settings_module, "restart_bridge", return_value=True) as restart_mock:
        _run(settings_module.receive_proxies_manual_text(update, context))

    restart_mock.assert_called_once()
    with get_session() as session:
        row = session.query(ProxyPoolEntry).one()
        assert row.protocol == ProxyProtocol.SHADOWSOCKS
    args, kwargs = reply.await_args
    assert "Xray" not in args[0]  # bridge_ok=True — предупреждения быть не должно


def test_receive_proxies_manual_text_warns_when_bridge_unavailable(db, tmp_path):
    reply = AsyncMock()
    uri = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpvWklvQTY5UTh5aGNRVjhrYTNQYTNB@82.38.31.26:8080"
    message = SimpleNamespace(text=uri, reply_text=reply)
    update = SimpleNamespace(message=message)
    registry = ProviderRegistry({})
    context = SimpleNamespace(
        user_data={"awaiting": "proxies_manual_add"},
        application=SimpleNamespace(bot_data=_bot_data(registry, tmp_path)),
    )

    with patch.object(settings_module, "restart_bridge", return_value=False):
        _run(settings_module.receive_proxies_manual_text(update, context))

    args, kwargs = reply.await_args
    assert "XRAY_PATH" in args[0]
