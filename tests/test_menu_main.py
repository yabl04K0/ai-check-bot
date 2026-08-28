"""🏠 Меню (menu.py::show_main_menu) — самая частая навигационная кнопка
во всём боте (есть почти в каждом nav_row). Два фикса из аудита меню:
1. edit_message_text теперь защищён от BadRequest (устаревшее сообщение
   / "message is not modified" при двойном тапе) с фолбэком на новое
   сообщение — раньше кнопка могла тихо зависать без единой реакции.
2. Уход через 🏠 Меню больше не оставляет 🗨 ИИ-чат "осиротевшим" в БД —
   см. app.bot.handlers.ai_chat.reset_stale_chat."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.error import BadRequest

from app.bot.handlers import menu as menu_module
from app.db.models import AiChatSession
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _context(user_data=None, admin_tg_id=None):
    settings = SimpleNamespace(admin_tg_id=admin_tg_id)
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"settings": settings}),
        user_data=user_data if user_data is not None else {},
    )


def _update(edit_message_text=None, effective_user_id=1):
    edit = edit_message_text or AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="menu:main")
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=effective_user_id),
        effective_chat=SimpleNamespace(send_message=AsyncMock()),
    )


def test_show_main_menu_edits_message_normally(db):
    context = _context()
    update = _update()

    _run(menu_module.show_main_menu(update, context))

    update.callback_query.edit_message_text.assert_awaited_once()
    update.effective_chat.send_message.assert_not_awaited()


def test_show_main_menu_falls_back_to_new_message_on_bad_request(db):
    failing_edit = AsyncMock(side_effect=BadRequest("Message is not modified"))
    context = _context()
    update = _update(edit_message_text=failing_edit)

    _run(menu_module.show_main_menu(update, context))

    update.effective_chat.send_message.assert_awaited_once()


def test_show_main_menu_closes_stale_ai_chat_session(db):
    with get_session() as session:
        chat = AiChatSession(tg_user_id="1", full_access=True)
        session.add(chat)
        session.flush()
        session_id = chat.id

    context = _context(user_data={"awaiting": "ai_chat", "ai_chat_session_id": session_id})
    update = _update()

    _run(menu_module.show_main_menu(update, context))

    assert context.user_data.get("awaiting") is None
    assert context.user_data.get("ai_chat_session_id") is None
    with get_session() as session:
        reloaded = session.get(AiChatSession, session_id)
        assert reloaded.closed_at is not None


def test_show_main_menu_leaves_unrelated_awaiting_alone_besides_flow(db):
    """flow всегда сбрасывается (визарды ЧЕК/Фичи и т.п. одноразовые по
    своей природе); awaiting не-ai_chat значений (например, ожидание ввода
    ключа провайдера) тоже должно сбрасываться в главное меню — это
    поведение НЕ менялось этим фиксом, просто проверяем отсутствие
    регрессии на не-чатовых awaiting."""
    context = _context(user_data={"awaiting": "provider_key:groq", "flow": {"selected": {1}}})
    update = _update()

    _run(menu_module.show_main_menu(update, context))

    assert context.user_data.get("awaiting") is None
    assert "flow" not in context.user_data
