"""🗨 Групповой ИИ-чат — один ход (app.ai_chat.orchestrator.run_turn):
общая история сообщений, делегирование под-вопроса другому тиру и
допуск к инструментам бота только при full_access=True (см. запрос
пользователя: "перед входом в такой чат спрашивать выдавать ли все
права")."""

from __future__ import annotations

from app.ai_chat.orchestrator import MAX_TOOL_STEPS, run_turn
from app.db.models import (
    AccountPriority,
    AiChatMessage,
    AiChatSession,
    Project,
    ProviderAccountStatus,
    ProviderName,
)
from app.db.session import get_session
from app.providers.base import AuthStatus, ProviderResult
from app.providers.registry import ProviderRegistry
from app.providers.tiers import set_delegation_mode, set_tier


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


def _make_session(*, full_access: bool) -> int:
    with get_session() as session:
        chat = AiChatSession(tg_user_id="1", full_access=full_access)
        session.add(chat)
        session.flush()
        return chat.id


def _messages(session_id: int) -> list[AiChatMessage]:
    with get_session() as session:
        from sqlalchemy import select

        return list(
            session.scalars(
                select(AiChatMessage).where(AiChatMessage.session_id == session_id).order_by(AiChatMessage.id)
            ).all()
        )


def test_run_turn_plain_text_answer_is_returned_and_saved(db):
    provider = _ScriptedProvider(ProviderName.CLAUDE_CODE, ["Привет! Чем помочь?"])
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    session_id = _make_session(full_access=False)

    reply = run_turn(session_id, "Привет", registry=registry, application=None, tg_user_id=1)

    assert reply == "Привет! Чем помочь?"
    messages = _messages(session_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].author == "claude_code:primary"


def test_run_turn_delegates_to_another_tier_account(db):
    head = _ScriptedProvider(
        ProviderName.CLAUDE_CODE,
        ["ДЕЙСТВИЕ: delegate | tier=delegation; prompt=Сколько будет 2+2?", "Итог: 4."],
    )
    delegatee = _ScriptedProvider(ProviderName.GROQ, ["4, это просто."])
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: head, ProviderName.GROQ: delegatee})
    set_delegation_mode(True)
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    session_id = _make_session(full_access=False)

    reply = run_turn(session_id, "Сколько 2+2?", registry=registry, application=None, tg_user_id=1)

    assert reply == "Итог: 4."
    assert delegatee.prompts
    roles = [m.role for m in _messages(session_id)]
    assert roles == ["user", "assistant", "tool", "assistant"]


def test_run_turn_denies_bot_tool_without_full_access(db):
    provider = _ScriptedProvider(
        ProviderName.CLAUDE_CODE,
        ["ДЕЙСТВИЕ: list_projects", "Понял, доступа нет — отвечу текстом."],
    )
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    session_id = _make_session(full_access=False)

    reply = run_turn(session_id, "покажи проекты", registry=registry, application=None, tg_user_id=1)

    assert "доступа нет" in reply
    tool_messages = [m for m in _messages(session_id) if m.role == "tool"]
    assert "не выдан полный доступ" in tool_messages[0].content


def test_run_turn_executes_bot_tool_with_full_access(db):
    with get_session() as session:
        session.add(Project(name="demo", repo_full_name="me/demo", local_path="/tmp/demo"))

    provider = _ScriptedProvider(
        ProviderName.CLAUDE_CODE, ["ДЕЙСТВИЕ: list_projects", "Вот твои проекты: demo."]
    )
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    session_id = _make_session(full_access=True)

    reply = run_turn(session_id, "покажи проекты", registry=registry, application=None, tg_user_id=1)

    assert reply == "Вот твои проекты: demo."
    tool_messages = [m for m in _messages(session_id) if m.role == "tool"]
    assert "demo" in tool_messages[0].content


def test_run_turn_stops_after_max_tool_steps(db):
    provider = _ScriptedProvider(ProviderName.CLAUDE_CODE, ["ДЕЙСТВИЕ: list_projects"] * (MAX_TOOL_STEPS + 2))
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    session_id = _make_session(full_access=True)

    reply = run_turn(session_id, "?", registry=registry, application=None, tg_user_id=1)

    assert "Слишком много действий" in reply


def test_run_turn_tracks_live_status_during_delegate_and_clears_after(db):
    """См. app.ai_chat.sessions.set_status/get_status — запрос
    пользователя: "улучши визуал выполнения всех команд". Проверяет и
    промежуточные статусы во время хода, и что после завершения статус
    сброшен (не остаётся висеть "🤝 Делегирую..." навсегда)."""
    from app.ai_chat.sessions import get_status

    head = _ScriptedProvider(
        ProviderName.CLAUDE_CODE,
        ["ДЕЙСТВИЕ: delegate | tier=delegation; prompt=Сколько будет 2+2?", "Итог: 4."],
    )
    delegatee = _ScriptedProvider(ProviderName.GROQ, ["4, это просто."])
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: head, ProviderName.GROQ: delegatee})
    set_delegation_mode(True)
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    session_id = _make_session(full_access=False)

    run_turn(session_id, "Сколько 2+2?", registry=registry, application=None, tg_user_id=1)

    # Финальный статус — очищен, ход завершён.
    assert get_status(session_id) is None


def test_run_turn_sets_descriptive_status_for_delegate_action(db, monkeypatch):
    from app.ai_chat import orchestrator as orchestrator_module

    recorded = []
    monkeypatch.setattr(
        orchestrator_module, "set_status", lambda session_id, detail: recorded.append(detail)
    )

    head = _ScriptedProvider(
        ProviderName.CLAUDE_CODE,
        ["ДЕЙСТВИЕ: delegate | tier=delegation; prompt=Сколько будет 2+2?", "Итог: 4."],
    )
    delegatee = _ScriptedProvider(ProviderName.GROQ, ["4, это просто."])
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: head, ProviderName.GROQ: delegatee})
    set_delegation_mode(True)
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    session_id = _make_session(full_access=False)

    run_turn(session_id, "Сколько 2+2?", registry=registry, application=None, tg_user_id=1)

    assert any("Делегирую" in text and "delegation" in text for text in recorded if text)
    assert recorded[-1] is None  # финальная очистка статуса


def test_run_turn_falls_back_to_first_connected_when_no_head_tier(db):
    provider = _ScriptedProvider(ProviderName.GEMINI, ["ок"])
    registry = ProviderRegistry({ProviderName.GEMINI: provider})
    session_id = _make_session(full_access=False)

    reply = run_turn(session_id, "привет", registry=registry, application=None, tg_user_id=1)

    assert reply == "ок"
    assert _messages(session_id)[1].author == "gemini:primary"
