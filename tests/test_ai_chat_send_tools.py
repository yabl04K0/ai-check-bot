from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.ai_chat.tools as tools_module
from app.ai_chat.tools import TOOLS, ToolContext
from app.db.models import Project
from app.db.session import get_session
from app.providers.registry import ProviderRegistry
from app.tasks.web_research import SearchResult


def _ctx(tg_user_id: int = 1) -> ToolContext:
    registry = ProviderRegistry({})
    application = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    )
    return ToolContext(registry=registry, application=application, tg_user_id=tg_user_id)


def _add_project(name: str, local_path: str) -> None:
    with get_session() as session:
        session.add(Project(name=name, repo_full_name=f"o/{name}", local_path=local_path))


def test_send_message_requires_text(db):
    ctx = _ctx()
    result = TOOLS["send_message"].handler(ctx, {})
    assert "нужен text" in result
    ctx.application.bot.send_message.assert_not_awaited()


def test_send_message_sends_to_tg_user(db):
    ctx = _ctx(tg_user_id=42)
    result = TOOLS["send_message"].handler(ctx, {"text": "привет"})
    assert result == "Отправлено."
    ctx.application.bot.send_message.assert_awaited_once_with(42, "привет")


def test_send_file_requires_project_and_path(db):
    ctx = _ctx()
    result = TOOLS["send_file"].handler(ctx, {"project": "demo"})
    assert "нужны project и path" in result


def test_send_file_unknown_project(db):
    ctx = _ctx()
    result = TOOLS["send_file"].handler(ctx, {"project": "ghost", "path": "a.txt"})
    assert "не найден" in result


def test_send_file_sends_existing_file(db, tmp_path):
    (tmp_path / "report.md").write_text("содержимое", encoding="utf-8")
    _add_project("demo", str(tmp_path))
    ctx = _ctx(tg_user_id=7)

    result = TOOLS["send_file"].handler(ctx, {"project": "demo", "path": "report.md"})

    assert "отправлен" in result
    ctx.application.bot.send_document.assert_awaited_once()
    args, kwargs = ctx.application.bot.send_document.call_args
    assert args[0] == 7
    assert kwargs["document"] == "содержимое".encode()
    assert kwargs["filename"] == "report.md"


def test_send_file_missing_file(db, tmp_path):
    _add_project("demo", str(tmp_path))
    ctx = _ctx()

    result = TOOLS["send_file"].handler(ctx, {"project": "demo", "path": "missing.txt"})

    assert "не найден" in result
    ctx.application.bot.send_document.assert_not_awaited()


def test_send_file_rejects_path_traversal_outside_project(db, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    _add_project("demo", str(project_dir))
    ctx = _ctx()

    result = TOOLS["send_file"].handler(ctx, {"project": "demo", "path": "../secret.txt"})

    assert "отказано" in result
    ctx.application.bot.send_document.assert_not_awaited()


def test_send_file_rejects_absolute_path_outside_project(db, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    _add_project("demo", str(project_dir))
    ctx = _ctx()

    result = TOOLS["send_file"].handler(ctx, {"project": "demo", "path": str(outside)})

    assert "отказано" in result
    ctx.application.bot.send_document.assert_not_awaited()


def test_web_search_requires_query(db):
    ctx = _ctx()
    result = TOOLS["web_search"].handler(ctx, {})
    assert "нужен query" in result


def test_web_search_formats_results(db, monkeypatch):
    monkeypatch.setattr(
        tools_module,
        "_web_search",
        lambda query, **kw: [SearchResult(title="T1", url="https://a.example", snippet="S1")],
    )
    ctx = _ctx()
    result = TOOLS["web_search"].handler(ctx, {"query": "что-то"})
    assert "T1" in result
    assert "https://a.example" in result
    assert "S1" in result


def test_web_search_reports_no_results(db, monkeypatch):
    monkeypatch.setattr(tools_module, "_web_search", lambda query, **kw: [])
    ctx = _ctx()
    result = TOOLS["web_search"].handler(ctx, {"query": "что-то"})
    assert "Ничего не найдено" in result


def test_fetch_url_requires_url(db):
    ctx = _ctx()
    result = TOOLS["fetch_url"].handler(ctx, {})
    assert "нужен url" in result


def test_fetch_url_returns_fetched_text(db, monkeypatch):
    monkeypatch.setattr(tools_module, "_fetch_url", lambda url, **kw: "текст страницы")
    ctx = _ctx()
    result = TOOLS["fetch_url"].handler(ctx, {"url": "https://example.com"})
    assert result == "текст страницы"
