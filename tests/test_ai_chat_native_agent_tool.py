"""run_native_agent — НАСТОЯЩИЙ агент Claude Code, вызываемый из 🗨 ИИ-чата
(см. app.ai_chat.tools._tool_run_native_agent, запрос пользователя:
"мне нужно что бы иишка могла запускать агенты на своей же подписке").
Двойной гейт: app.providers.ai_autonomy.ai_native_agents_enabled (тула
не существует вообще, если выключено) и, если автоодобрение выключено,
подтверждение конкретно ЭТОГО запуска (см. app.ai_chat.approvals)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ai_chat import agent_activity
from app.ai_chat import tools as tools_module
from app.ai_chat.approvals import DECISION_ALLOW, DECISION_ALWAYS, DECISION_DEFER, DECISION_DENY
from app.ai_chat.tools import TOOLS, ToolContext
from app.db.models import AiChatMessage, AiChatSession, Project, ProviderAccountStatus, ProviderName
from app.db.session import get_session
from app.providers.agent_permissions import native_agent_always_allowed
from app.providers.ai_autonomy import set_ai_command_auto_approve, set_ai_native_agents_enabled
from app.providers.base import AuthStatus, ProviderResult
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.providers.registry import ProviderRegistry


def _add_project(local_path: str | None = "/tmp/demo") -> None:
    with get_session() as session:
        session.add(Project(name="demo", repo_full_name="o/demo", local_path=local_path))


class _FakeClaudeCode(ClaudeCodeCliProvider):
    def __init__(self):
        super().__init__("claude", oauth_token="tok")
        self.calls = []

    def run_agentic_task(self, prompt, project_path, *, account_label=None, can_edit=True):
        self.calls.append((prompt, project_path, account_label, can_edit))
        return ProviderResult(text="агент справился")


class _NonAgenticProvider:
    name = ProviderName.CLAUDE_CODE

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)


def _ctx(provider, tg_user_id: int = 1, session_id: int | None = None) -> ToolContext:
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: provider})
    application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    return ToolContext(
        registry=registry, application=application, tg_user_id=tg_user_id, session_id=session_id
    )


def _make_session(tg_user_id: str = "1") -> int:
    with get_session() as session:
        chat = AiChatSession(tg_user_id=tg_user_id, full_access=True)
        session.add(chat)
        session.flush()
        return chat.id


def _add_message(session_id: int, role: str, content: str, author: str | None = None) -> None:
    with get_session() as session:
        session.add(AiChatMessage(session_id=session_id, role=role, content=content, author=author))


def test_run_native_agent_disabled_by_default(db, monkeypatch, tmp_path):
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert "выключено" in result
    assert provider.calls == []


def test_run_native_agent_requires_project_and_task(db):
    set_ai_native_agents_enabled(True)
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo"})

    assert "нужны project и task" in result


def test_run_native_agent_project_not_found(db):
    set_ai_native_agents_enabled(True)
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "ghost", "task": "почини баг"})

    assert "не найден" in result


def test_run_native_agent_project_without_local_path(db):
    set_ai_native_agents_enabled(True)
    _add_project(local_path=None)
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert "local_path" in result


def test_run_native_agent_wrong_provider_type(db, tmp_path):
    set_ai_native_agents_enabled(True)
    _add_project(str(tmp_path))
    ctx = _ctx(_NonAgenticProvider())

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert "недоступен" in result


def test_run_native_agent_auto_approve_runs_immediately(db, tmp_path):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(True)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert provider.calls == [("почини баг", str(tmp_path), None, True)]
    ctx.application.bot.send_message.assert_not_awaited()


def test_run_native_agent_includes_recent_chat_context(db, tmp_path):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(True)
    _add_project(str(tmp_path))
    session_id = _make_session()
    _add_message(session_id, "user", "у нас проект demo на FastAPI, база SQLite")
    _add_message(session_id, "assistant", "понял, учту это", author="claude_code:primary")
    provider = _FakeClaudeCode()
    ctx = _ctx(provider, session_id=session_id)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert len(provider.calls) == 1
    sent_task, project_path, account_label, can_edit = provider.calls[0]
    assert "у нас проект demo на FastAPI, база SQLite" in sent_task
    assert "почини баг" in sent_task
    assert project_path == str(tmp_path)
    assert account_label is None
    assert can_edit is True


def test_run_native_agent_without_session_id_uses_task_unchanged(db, tmp_path):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(True)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert provider.calls == [("почини баг", str(tmp_path), None, True)]


def test_run_native_agent_with_session_id_but_no_history_uses_task_unchanged(db, tmp_path):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(True)
    _add_project(str(tmp_path))
    session_id = _make_session()
    provider = _FakeClaudeCode()
    ctx = _ctx(provider, session_id=session_id)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert provider.calls == [("почини баг", str(tmp_path), None, True)]


def test_run_native_agent_asks_and_runs_when_approved(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(False)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    monkeypatch.setattr(tools_module, "wait_for_decision", lambda token, **kw: DECISION_ALLOW)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert provider.calls
    ctx.application.bot.send_message.assert_awaited_once()
    args, kwargs = ctx.application.bot.send_message.await_args
    assert args[0] == ctx.tg_user_id
    assert "demo" in args[1]
    assert native_agent_always_allowed("demo") is False


def test_run_native_agent_rejected(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(False)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    monkeypatch.setattr(tools_module, "wait_for_decision", lambda token, **kw: DECISION_DENY)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert "отклонено" in result
    assert provider.calls == []
    assert native_agent_always_allowed("demo") is False


def test_run_native_agent_deferred(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(False)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    monkeypatch.setattr(tools_module, "wait_for_decision", lambda token, **kw: DECISION_DEFER)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert "отложено" in result
    assert "отклонено" not in result
    assert "истекло время" not in result
    assert provider.calls == []
    assert native_agent_always_allowed("demo") is False


def test_run_native_agent_always_allow_persists_and_skips_prompt_next_time(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(False)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    monkeypatch.setattr(tools_module, "wait_for_decision", lambda token, **kw: DECISION_ALWAYS)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert len(provider.calls) == 1
    assert native_agent_always_allowed("demo") is True
    ctx.application.bot.send_message.assert_awaited_once()

    def _must_not_be_called(token, **kw):
        raise AssertionError("wait_for_decision не должен вызываться второй раз для того же проекта")

    monkeypatch.setattr(tools_module, "wait_for_decision", _must_not_be_called)

    result2 = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини ещё раз"})

    assert result2 == "агент справился"
    assert len(provider.calls) == 2
    ctx.application.bot.send_message.assert_awaited_once()


def test_run_native_agent_timeout(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(False)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    monkeypatch.setattr(tools_module, "wait_for_decision", lambda token, **kw: None)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert "истекло время" in result
    assert provider.calls == []


def test_run_native_agent_registers_and_clears_activity_on_success(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(True)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()
    ctx = _ctx(provider)

    calls = []
    original_start = agent_activity.start
    original_finish = agent_activity.finish

    def spy_start(project, task):
        activity_id = original_start(project, task)
        calls.append(("start", activity_id))
        assert len(agent_activity.active()) == 1
        return activity_id

    def spy_finish(activity_id):
        calls.append(("finish", activity_id))
        original_finish(activity_id)

    monkeypatch.setattr(agent_activity, "start", spy_start)
    monkeypatch.setattr(agent_activity, "finish", spy_finish)

    result = TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})

    assert result == "агент справился"
    assert [kind for kind, _ in calls] == ["start", "finish"]
    assert calls[0][1] == calls[1][1]
    assert agent_activity.active() == []


def test_run_native_agent_clears_activity_when_provider_raises(db, tmp_path, monkeypatch):
    set_ai_native_agents_enabled(True)
    set_ai_command_auto_approve(True)
    _add_project(str(tmp_path))
    provider = _FakeClaudeCode()

    def _boom(*args, **kwargs):
        raise RuntimeError("агент упал")

    provider.run_agentic_task = _boom
    ctx = _ctx(provider)

    calls = []
    original_start = agent_activity.start
    original_finish = agent_activity.finish

    def spy_start(project, task):
        activity_id = original_start(project, task)
        calls.append(("start", activity_id))
        return activity_id

    def spy_finish(activity_id):
        calls.append(("finish", activity_id))
        original_finish(activity_id)

    monkeypatch.setattr(agent_activity, "start", spy_start)
    monkeypatch.setattr(agent_activity, "finish", spy_finish)

    try:
        TOOLS["run_native_agent"].handler(ctx, {"project": "demo", "task": "почини баг"})
    except RuntimeError:
        pass

    assert [kind for kind, _ in calls] == ["start", "finish"]
    assert calls[0][1] == calls[1][1]
    assert agent_activity.active() == []
