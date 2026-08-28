"""DM владельцу с кнопкой ОК про события пула прокси — см.
app/proxies/alerts.py::notify_admin, app.bot.keyboards.dismiss_menu."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.proxies.alerts import notify_admin


def _run(coro):
    return asyncio.run(coro)


def test_notify_admin_sends_with_ok_button():
    send = AsyncMock()
    application = SimpleNamespace(
        bot_data={"settings": SimpleNamespace(admin_tg_id=42)}, bot=SimpleNamespace(send_message=send)
    )

    _run(notify_admin(application, "🔁 прокси заменён"))

    send.assert_awaited_once()
    args, kwargs = send.await_args
    assert args[0] == 42
    assert args[1] == "🔁 прокси заменён"
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "dismiss" in callbacks


def test_notify_admin_noop_without_admin_id():
    send = AsyncMock()
    application = SimpleNamespace(
        bot_data={"settings": SimpleNamespace(admin_tg_id=None)}, bot=SimpleNamespace(send_message=send)
    )

    _run(notify_admin(application, "текст"))

    send.assert_not_awaited()
