from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.ai_chat.compact import (
    COMPACT_AUTHOR,
    COMPACT_THRESHOLD_CHARS,
    KEEP_RECENT_MESSAGES,
    maybe_compact,
)
from app.ai_chat.orchestrator import run_turn
from app.db.models import AiChatMessage, AiChatSession, ProviderAccountStatus, ProviderName
from app.db.session import get_session
from app.providers.base import AuthStatus, ProviderError, ProviderResult
from app.providers.registry import ProviderRegistry


class _ScriptedProvider:
    def __init__(self, name, responses):
        self.name = name
        self._responses = list(responses)
        self.prompts = []

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None):
        self.prompts.append((prompt, options))
        text = self._responses.pop(0) if self._responses else "(пусто)"
        return ProviderResult(text=text)


class _FailingProvider:
    def __init__(self, name):
        self.name = name

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None):
        raise ProviderError("квота исчерпана")


def _make_session(*, full_access: bool = False) -> int:
    with get_session() as session:
        chat = AiChatSession(tg_user_id="1", full_access=full_access)
        session.add(chat)
        session.flush()
        return chat.id


def _messages(session_id: int) -> list[AiChatMessage]:
    with get_session() as session:
        return list(
            session.scalars(
                select(AiChatMessage).where(AiChatMessage.session_id == session_id).order_by(AiChatMessage.id)
            ).all()
        )


def _insert_messages(session_id: int, count: int, *, content_len: int) -> None:
    with get_session() as session:
        for i in range(count):
            session.add(
                AiChatMessage(
                    session_id=session_id, role="user", content=f"{i}:" + ("x" * content_len)
                )
            )


def _application():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


def test_below_threshold_conversation_is_left_untouched(db):
    session_id = _make_session()
    _insert_messages(session_id, 20, content_len=100)
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: _ScriptedProvider(ProviderName.CLAUDE_CODE, [])})
    application = _application()

    result = maybe_compact(session_id, registry=registry, application=application, tg_user_id=1)

    assert result is False
    assert len(_messages(session_id)) == 20
    application.bot.send_message.assert_not_awaited()


def test_few_messages_with_one_huge_message_is_not_compacted(db):
    session_id = _make_session()
    _insert_messages(session_id, 3, content_len=COMPACT_THRESHOLD_CHARS + 1000)
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: _ScriptedProvider(ProviderName.CLAUDE_CODE, [])})
    application = _application()

    result = maybe_compact(session_id, registry=registry, application=application, tg_user_id=1)

    assert result is False
    assert len(_messages(session_id)) == 3
    application.bot.send_message.assert_not_awaited()


def test_over_threshold_triggers_compaction(db):
    session_id = _make_session()
    per_message = (COMPACT_THRESHOLD_CHARS // 15) + 100
    _insert_messages(session_id, 20, content_len=per_message)
    before = _messages(session_id)
    kept_before = before[-KEEP_RECENT_MESSAGES:]

    provider = _ScriptedProvider(ProviderName.CLAUDE_CODE, ["Краткий пересказ старой истории."])
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    application = _application()

    result = maybe_compact(session_id, registry=registry, application=application, tg_user_id=42)

    assert result is True
    after = _messages(session_id)
    assert len(after) == KEEP_RECENT_MESSAGES + 1

    kept_after = after[-KEEP_RECENT_MESSAGES:]
    assert [m.id for m in kept_after] == [m.id for m in kept_before]
    assert [m.content for m in kept_after] == [m.content for m in kept_before]

    summary = after[0]
    assert summary.author == COMPACT_AUTHOR
    assert summary.role == "assistant"
    assert "Краткий пересказ старой истории." in summary.content
    assert summary.id == before[0].id

    application.bot.send_message.assert_awaited_once()
    args, _ = application.bot.send_message.call_args
    assert args[0] == 42
    assert "🗜" in args[1]


def test_compaction_then_fresh_run_turn_does_not_crash(db):
    session_id = _make_session(full_access=False)
    per_message = (COMPACT_THRESHOLD_CHARS // 15) + 100
    _insert_messages(session_id, 20, content_len=per_message)

    provider = _ScriptedProvider(
        ProviderName.CLAUDE_CODE, ["Краткий пересказ старой истории.", "Продолжаю разговор."]
    )
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    application = _application()

    reply = run_turn(session_id, "Что дальше?", registry=registry, application=application, tg_user_id=1)

    assert reply == "Продолжаю разговор."
    messages = _messages(session_id)
    assert any(m.author == COMPACT_AUTHOR for m in messages)
    application.bot.send_message.assert_awaited_once()


def test_provider_error_during_summarization_leaves_history_unchanged(db):
    session_id = _make_session()
    per_message = (COMPACT_THRESHOLD_CHARS // 15) + 100
    _insert_messages(session_id, 20, content_len=per_message)
    before = _messages(session_id)

    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: _FailingProvider(ProviderName.CLAUDE_CODE)})
    application = _application()

    result = maybe_compact(session_id, registry=registry, application=application, tg_user_id=1)

    assert result is False
    after = _messages(session_id)
    assert [m.id for m in after] == [m.id for m in before]
    assert [m.content for m in after] == [m.content for m in before]
    application.bot.send_message.assert_not_awaited()


def test_summary_message_sorts_before_kept_recent_messages(db):
    session_id = _make_session()
    per_message = (COMPACT_THRESHOLD_CHARS // 15) + 100
    _insert_messages(session_id, 20, content_len=per_message)

    provider = _ScriptedProvider(ProviderName.CLAUDE_CODE, ["Пересказ."])
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    application = _application()

    maybe_compact(session_id, registry=registry, application=application, tg_user_id=1)

    ordered = _messages(session_id)
    summary_index = next(i for i, m in enumerate(ordered) if m.author == COMPACT_AUTHOR)
    assert summary_index == 0
    for other in ordered[1:]:
        assert ordered[summary_index].id < other.id
