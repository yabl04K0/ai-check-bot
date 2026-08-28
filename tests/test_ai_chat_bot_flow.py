"""🗨 Групповой ИИ-чат — экран согласия на полный доступ, старт/закрытие
чата, роутинг текстовых сообщений (см. app/bot/handlers/ai_chat.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import ai_chat as ai_chat_module
from app.db.models import AiChatSession, ProviderAccountStatus, ProviderName
from app.db.session import get_session
from app.providers.base import AuthStatus, ProviderResult
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


class _FakeProvider:
    name = ProviderName.CLAUDE_CODE

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None):
        return ProviderResult(text="Привет!")


def _update(data: str, admin_tg_id: int = 1):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id)), query


def _chat_message(text: str) -> SimpleNamespace:
    """reply_text должен возвращать объект с async edit_text/delete —
    это статус-сообщение "⏳ Думаю…" (см. _run_turn_and_reply/_poll_status),
    голый AsyncMock().return_value этого не гарантирует."""
    status_message = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(text=text, reply_text=AsyncMock(return_value=status_message))


def _context(user_data=None):
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: _FakeProvider()})
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"provider_registry": registry}),
        user_data=user_data if user_data is not None else {},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )


def test_start_ai_chat_shows_disclaimer(db):
    update, query = _update("menu:ai_chat")
    _run(ai_chat_module.start_ai_chat(update, _context()))

    (text,), kwargs = query.edit_message_text.await_args
    assert "Полный доступ" in text


def test_create_session_full_access_marks_flag_and_sets_awaiting(db):
    update, query = _update("aichat:new:full")
    context = _context()
    _run(ai_chat_module.create_ai_chat_session(update, context))

    assert context.user_data["awaiting"] == "ai_chat"
    session_id = context.user_data["ai_chat_session_id"]
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        assert chat.full_access is True


def test_create_session_limited_access(db):
    update, query = _update("aichat:new:limited")
    context = _context()
    _run(ai_chat_module.create_ai_chat_session(update, context))

    session_id = context.user_data["ai_chat_session_id"]
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        assert chat.full_access is False


def test_close_ai_chat_clears_awaiting_and_marks_closed(db):
    update, query = _update("aichat:new:limited")
    context = _context()
    _run(ai_chat_module.create_ai_chat_session(update, context))
    session_id = context.user_data["ai_chat_session_id"]

    close_update, close_query = _update("aichat:close")
    _run(ai_chat_module.close_ai_chat(close_update, context))

    assert context.user_data["awaiting"] is None
    assert context.user_data["ai_chat_session_id"] is None
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        assert chat.closed_at is not None


def test_receive_text_ignored_when_not_awaiting_ai_chat(db):
    context = _context(user_data={"awaiting": None})
    message = SimpleNamespace(text="привет", reply_text=AsyncMock())
    update = SimpleNamespace(
        message=message, effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(id=1)
    )

    _run(ai_chat_module.receive_ai_chat_text(update, context))

    message.reply_text.assert_not_awaited()


def test_receive_text_replies_via_orchestrator(db):
    update, query = _update("aichat:new:limited")
    context = _context()
    _run(ai_chat_module.create_ai_chat_session(update, context))

    message = _chat_message("привет")
    msg_update = SimpleNamespace(
        message=message, effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(id=1)
    )

    async def _drive():
        await ai_chat_module.receive_ai_chat_text(msg_update, context)
        # receive_ai_chat_text планирует фактический ход отдельной
        # asyncio.create_task (не блокирует диспетчер PTB на время
        # run_turn) — даём этой фоновой задаче отработать в том же луп.
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending)

    _run(_drive())

    # Первый reply_text — статус-сообщение "⏳ Думаю…", второй — сам ответ.
    assert message.reply_text.await_count == 2
    (text,), kwargs = message.reply_text.await_args_list[-1]
    assert text == "Привет!"


async def _drive(coro):
    await coro
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending)


def test_receive_text_chunks_long_reply(db):
    """reply[:4000] раньше молча терял хвост длинных ответов — теперь
    бьётся на чанки по 3800 символов, как commit_show_diff в check.py."""

    class _LongReplyProvider(_FakeProvider):
        def run_prompt(self, prompt, options=None):
            return ProviderResult(text="x" * 9000)

    update, query = _update("aichat:new:limited")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"provider_registry": ProviderRegistry({ProviderName.CLAUDE_CODE: _LongReplyProvider()})}
        ),
        user_data={},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    _run(ai_chat_module.create_ai_chat_session(update, context))

    message = _chat_message("привет")
    msg_update = SimpleNamespace(
        message=message, effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(id=1)
    )

    _run(_drive(ai_chat_module.receive_ai_chat_text(msg_update, context)))

    # Первый reply_text — статус-сообщение "⏳ Думаю…", следующие 3 — чанки ответа.
    assert message.reply_text.await_count == 4
    calls = message.reply_text.await_args_list[1:]
    assert sum(len(c.args[0]) for c in calls) == 9000
    assert "reply_markup" not in calls[0].kwargs
    assert "reply_markup" not in calls[1].kwargs
    assert calls[2].kwargs.get("reply_markup") is ai_chat_module.CLOSE_CHAT_MARKUP


def test_receive_text_reports_error_and_keeps_chat_open(db):
    class _BrokenProvider(_FakeProvider):
        def run_prompt(self, prompt, options=None):
            raise RuntimeError("boom")

    update, query = _update("aichat:new:limited")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"provider_registry": ProviderRegistry({ProviderName.CLAUDE_CODE: _BrokenProvider()})}
        ),
        user_data={},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    _run(ai_chat_module.create_ai_chat_session(update, context))

    message = _chat_message("привет")
    msg_update = SimpleNamespace(
        message=message, effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(id=1)
    )

    _run(_drive(ai_chat_module.receive_ai_chat_text(msg_update, context)))

    # Первый reply_text — статус-сообщение "⏳ Думаю…", второй — сообщение об ошибке.
    assert message.reply_text.await_count == 2
    (text,), kwargs = message.reply_text.await_args_list[-1]
    assert "Не удалось получить ответ" in text
    assert kwargs.get("reply_markup") is ai_chat_module.CLOSE_CHAT_MARKUP
    # сессия/awaiting не тронуты — чат остаётся открытым для повторной попытки
    assert context.user_data["awaiting"] == "ai_chat"


def test_poll_status_edits_message_with_live_detail(db, monkeypatch):
    """_poll_status — живой индикатор хода (см. app.ai_chat.sessions.set_status,
    запрос пользователя: "улучши визуал выполнения всех команд" — раньше
    единственной обратной связью на время всего хода был статичный
    индикатор "печатает…")."""
    from app.ai_chat.sessions import set_status

    monkeypatch.setattr(ai_chat_module, "STATUS_POLL_SECONDS", 0.01)
    with get_session() as session:
        chat = AiChatSession(tg_user_id="1", full_access=False)
        session.add(chat)
        session.flush()
        session_id = chat.id

    set_status(session_id, "🔧 Выполняю: list_projects…")
    message = SimpleNamespace(edit_text=AsyncMock())

    async def _drive():
        task = asyncio.create_task(ai_chat_module._poll_status(session_id, message))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(_drive())

    message.edit_text.assert_awaited()
    texts = [c.args[0] for c in message.edit_text.await_args_list]
    assert any("list_projects" in t for t in texts)


def test_stop_status_poll_cancels_task_and_deletes_message(db):
    async def _drive():
        task = asyncio.create_task(asyncio.sleep(10))
        message = SimpleNamespace(delete=AsyncMock())
        await ai_chat_module._stop_status_poll(task, message)
        return task, message

    task, message = _run(_drive())

    assert task.cancelled()
    message.delete.assert_awaited_once()


def test_approve_native_agent_resolves_pending_token(db):
    from app.ai_chat.approvals import DECISION_ALLOW, create_pending, wait_for_decision

    token = create_pending()
    update, query = _update(f"aichat:agent_yes:{token}")

    _run(ai_chat_module.approve_native_agent(update, _context()))

    assert wait_for_decision(token, timeout=0) == DECISION_ALLOW
    args, kwargs = query.edit_message_text.await_args
    assert "Разрешено" in args[0]


def test_reject_native_agent_resolves_pending_token(db):
    from app.ai_chat.approvals import DECISION_DENY, create_pending, wait_for_decision

    token = create_pending()
    update, query = _update(f"aichat:agent_no:{token}")

    _run(ai_chat_module.reject_native_agent(update, _context()))

    assert wait_for_decision(token, timeout=0) == DECISION_DENY
    args, kwargs = query.edit_message_text.await_args
    assert "Отклонено" in args[0]


def test_always_allow_native_agent_resolves_pending_token(db):
    from app.ai_chat.approvals import DECISION_ALWAYS, create_pending, wait_for_decision

    token = create_pending()
    update, query = _update(f"aichat:agent_always:{token}")

    _run(ai_chat_module.always_allow_native_agent(update, _context()))

    assert wait_for_decision(token, timeout=0) == DECISION_ALWAYS
    args, kwargs = query.edit_message_text.await_args
    assert "Разрешено" in args[0]


def test_defer_native_agent_resolves_pending_token(db):
    from app.ai_chat.approvals import DECISION_DEFER, create_pending, wait_for_decision

    token = create_pending()
    update, query = _update(f"aichat:agent_defer:{token}")

    _run(ai_chat_module.defer_native_agent(update, _context()))

    assert wait_for_decision(token, timeout=0) == DECISION_DEFER
    args, kwargs = query.edit_message_text.await_args
    assert "Отложено" in args[0]
