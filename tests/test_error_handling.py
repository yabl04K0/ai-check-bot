"""Без глобального error handler любое необработанное исключение в
хендлере PTB просто логируется в stderr и молчит в чате — юзер не видит
ничего. Тестируем сам handle_error и конкретный кейс, который раньше
падал бы: добавление проекта с уже занятым repo_full_name."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import Chat, Message, Update

from app.bot.error_handler import USER_FACING_ERROR, handle_error
from app.bot.handlers.projects import on_text
from app.db.models import ActionLog, Project
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def test_add_project_duplicate_repo_gives_friendly_message_not_crash(db):
    with get_session() as session:
        session.add(Project(name="Existing", repo_full_name="owner/dup"))
        session.commit()

    reply = AsyncMock()
    update = SimpleNamespace(message=SimpleNamespace(text="New; owner/dup", reply_text=reply))
    context = SimpleNamespace(user_data={"awaiting": "add_project"})

    _run(on_text(update, context))

    reply.assert_awaited_once()
    (text,), _ = reply.await_args
    assert "уже есть" in text
    assert context.user_data["awaiting"] is None

    with get_session() as session:
        count = session.query(Project).filter_by(repo_full_name="owner/dup").count()
    assert count == 1  # не задвоилось


def test_handle_error_notifies_chat_and_logs_action(db):
    chat = Chat(id=555, type="private")
    message = Message(message_id=1, date=dt.datetime.now(dt.timezone.utc), chat=chat)
    update = Update(update_id=1, message=message)

    send_message = AsyncMock()
    context = SimpleNamespace(error=RuntimeError("boom"), bot=SimpleNamespace(send_message=send_message))

    _run(handle_error(update, context))

    send_message.assert_awaited_once_with(555, USER_FACING_ERROR)

    with get_session() as session:
        entries = session.query(ActionLog).filter_by(action="unhandled_error").all()
    assert len(entries) == 1
    assert "boom" in entries[0].details


def test_handle_error_without_chat_does_not_crash(db):
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(error=RuntimeError("no chat here"), bot=bot)
    # update не Update-объект (например, ошибка вне обработки конкретного апдейта) — не должно падать
    _run(handle_error(None, context))

    with get_session() as session:
        entries = session.query(ActionLog).filter_by(action="unhandled_error").all()
    assert len(entries) == 1
