"""📜 Мои чаты — список прошлых сессий 🗨 ИИ-чата и повторное использование
старого чата вместо обязательного старта с нуля (см. запрос пользователя:
"сделай схему чатов что бы можно было использовать старый чат снова в
будущем"). Проверяет: список фильтруется по владельцу, пагинация,
resume реально продолжает СТАРУЮ историю (не создаёт новую сессию), при
переключении закрывает другой активный чат, чужой session_id отклоняется."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import ai_chat as ai_chat_module
from app.db.models import AiChatMessage, AiChatSession
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _update(data: str, user_id: int = 1):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id)), query


def _context(user_data=None):
    return SimpleNamespace(user_data=user_data if user_data is not None else {})


def _make_session(tg_user_id: str, *, full_access: bool = False, closed: bool = True) -> int:
    from datetime import datetime, timezone

    with get_session() as session:
        chat = AiChatSession(
            tg_user_id=tg_user_id,
            full_access=full_access,
            closed_at=datetime.now(timezone.utc) if closed else None,
        )
        session.add(chat)
        session.flush()
        return chat.id


def _add_message(session_id: int, role: str, content: str, author: str | None = None) -> None:
    with get_session() as session:
        session.add(AiChatMessage(session_id=session_id, role=role, content=content, author=author))


def test_show_chat_history_empty_for_new_user(db):
    update, query = _update("aichat:history", user_id=1)
    _run(ai_chat_module.show_chat_history(update, _context()))

    args, kwargs = query.edit_message_text.await_args
    assert "нет ни одного чата" in args[0]


def test_show_chat_history_lists_only_own_sessions(db):
    my_id = _make_session("1")
    _make_session("2")  # чужая сессия — не должна попасть в список

    update, query = _update("aichat:history", user_id=1)
    _run(ai_chat_module.show_chat_history(update, _context()))

    args, kwargs = query.edit_message_text.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"aichat:resume:{my_id}" in callbacks
    assert not any(cb.startswith("aichat:resume:") and cb != f"aichat:resume:{my_id}" for cb in callbacks)


def test_show_chat_history_shows_preview_from_first_user_message(db):
    session_id = _make_session("1")
    _add_message(session_id, "user", "почини баг в auth.py")

    update, query = _update("aichat:history", user_id=1)
    _run(ai_chat_module.show_chat_history(update, _context()))

    args, kwargs = query.edit_message_text.await_args
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any("почини баг" in label for label in labels)


def test_show_chat_history_paginates_beyond_page_size(db):
    for _ in range(10):
        _make_session("1")

    update, query = _update("aichat:history", user_id=1)
    _run(ai_chat_module.show_chat_history(update, _context()))

    args, kwargs = query.edit_message_text.await_args
    assert "стр. 1/2" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "aichat:hist:page:1" in callbacks


def test_resume_chat_session_reopens_and_preserves_history(db):
    session_id = _make_session("1", full_access=True, closed=True)
    _add_message(session_id, "user", "старый вопрос")
    _add_message(session_id, "assistant", "старый ответ", author="claude_code:primary")

    update, query = _update(f"aichat:resume:{session_id}", user_id=1)
    context = _context()
    _run(ai_chat_module.resume_chat_session(update, context))

    assert context.user_data["awaiting"] == "ai_chat"
    assert context.user_data["ai_chat_session_id"] == session_id
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        assert chat.closed_at is None
        # История не пересоздана — то же количество сообщений, что и было.
        assert session.query(AiChatMessage).filter_by(session_id=session_id).count() == 2

    args, kwargs = query.edit_message_text.await_args
    assert "старый вопрос" in args[0]
    assert "старый ответ" in args[0]
    assert kwargs["reply_markup"] is ai_chat_module.CLOSE_CHAT_MARKUP


def test_resume_chat_session_closes_other_active_chat_first(db):
    old_session_id = _make_session("1", closed=False)
    other_session_id = _make_session("1", closed=True)

    update, query = _update(f"aichat:resume:{other_session_id}", user_id=1)
    context = _context(user_data={"awaiting": "ai_chat", "ai_chat_session_id": old_session_id})
    _run(ai_chat_module.resume_chat_session(update, context))

    with get_session() as session:
        old_chat = session.get(AiChatSession, old_session_id)
        assert old_chat.closed_at is not None  # закрыт при переключении
        new_chat = session.get(AiChatSession, other_session_id)
        assert new_chat.closed_at is None
    assert context.user_data["ai_chat_session_id"] == other_session_id


def test_resume_chat_session_rejects_foreign_session(db):
    foreign_id = _make_session("999")

    update, query = _update(f"aichat:resume:{foreign_id}", user_id=1)
    context = _context()
    _run(ai_chat_module.resume_chat_session(update, context))

    query.answer.assert_awaited_once()
    args, kwargs = query.answer.await_args
    assert "не найден" in args[0]
    query.edit_message_text.assert_not_awaited()
    assert "awaiting" not in context.user_data


def test_history_button_present_on_disclaimer_screen(db):
    update, query = _update("menu:ai_chat", user_id=1)
    _run(ai_chat_module.start_ai_chat(update, _context()))

    args, kwargs = query.edit_message_text.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "aichat:history" in callbacks
