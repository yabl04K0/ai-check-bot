"""_ensure_user раньше не сообщал, новый пользователь или нет — cmd_start
определял первый /start по in-memory bot_data["known_users"], которое
обнуляется при каждом рестарте бота: вернувшийся пользователь после
любого редеплоя снова видел приветственный текст с кредитом автора.
Теперь источник истины — сама таблица User, которая переживает рестарт."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.start import _ensure_user, cmd_start
from app.db.models import AiChatSession, User
from app.db.session import get_session


def test_ensure_user_returns_true_for_brand_new_user(db):
    is_new = _ensure_user(111, "Alice", False)

    assert is_new is True
    with get_session() as session:
        assert session.query(User).filter_by(tg_id=111).count() == 1


def test_ensure_user_returns_false_for_returning_user(db):
    _ensure_user(111, "Alice", False)

    is_new_second_call = _ensure_user(111, "Alice", False)

    assert is_new_second_call is False


def test_ensure_user_survives_simulated_restart(db):
    """Ключевой сценарий бага: 'рестарт' — это просто новый вызов без
    какого-либо in-memory состояния, только БД. Второй /start того же
    юзера не должен снова считаться первым."""
    first_call = _ensure_user(42, "Bob", False)
    # ничего похожего на bot_data["known_users"] здесь нет и не должно быть
    second_call_after_restart = _ensure_user(42, "Bob", False)

    assert first_call is True
    assert second_call_after_restart is False


def test_cmd_start_closes_stale_ai_chat_session(db):
    """/start — естественный способ "сбросить и вернуться в меню", раньше
    вообще не трогал context.user_data: активный 🗨 ИИ-чат оставался бы
    висеть в БД активным навсегда (см. app.bot.handlers.ai_chat.reset_stale_chat,
    аудит меню)."""
    with get_session() as session:
        chat = AiChatSession(tg_user_id="99", full_access=False)
        session.add(chat)
        session.flush()
        session_id = chat.id

    settings = SimpleNamespace(admin_tg_id=None)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"settings": settings}),
        user_data={"awaiting": "ai_chat", "ai_chat_session_id": session_id},
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99, full_name="Bob"),
        effective_chat=SimpleNamespace(send_message=AsyncMock()),
    )

    asyncio.run(cmd_start(update, context))

    assert context.user_data["awaiting"] is None
    assert context.user_data["ai_chat_session_id"] is None
    with get_session() as session:
        reloaded = session.get(AiChatSession, session_id)
        assert reloaded.closed_at is not None
