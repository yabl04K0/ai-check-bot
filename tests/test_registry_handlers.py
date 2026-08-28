"""📜 Реестр — постраничный список проектов и находок вместо жёсткого
обрезания на N без доступа к остальным (см. app/bot/keyboards.py::paginate_rows)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import InlineKeyboardMarkup

from app.bot.handlers import registry as registry_module
from app.db.models import Finding, FindingStatus, Project
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _update_and_context(callback_data: str):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=callback_data)
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={})
    return update, context, edit


def _flat_callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_show_registry_projects_paginates_beyond_page_size(db):
    with get_session() as session:
        for i in range(10):
            session.add(Project(name=f"P{i}", repo_full_name=f"o/p{i}"))

    update, context, edit = _update_and_context("menu:registry")

    _run(registry_module.show_registry_projects(update, context))

    args, kwargs = edit.await_args
    markup = kwargs["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    assert "стр." in args[0]
    assert "reg:projpage:1" in _flat_callbacks(markup)


def test_show_registry_projects_page_navigates(db):
    with get_session() as session:
        for i in range(10):
            session.add(Project(name=f"P{i}", repo_full_name=f"o/p{i}"))

    update, context, edit = _update_and_context("reg:projpage:1")

    _run(registry_module.show_registry_projects_page(update, context))

    args, kwargs = edit.await_args
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any("P9" in label for label in labels)  # 2-я страница — хвост списка


def test_show_registry_projects_empty_uses_wrapped_markup(db):
    update, context, edit = _update_and_context("menu:registry")

    _run(registry_module.show_registry_projects(update, context))

    args, kwargs = edit.await_args
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)


def _make_project_with_findings(count: int) -> int:
    with get_session() as session:
        project = Project(name="P", repo_full_name="o/p")
        session.add(project)
        session.flush()
        for i in range(count):
            session.add(
                Finding(
                    project_id=project.id,
                    status=FindingStatus.OPEN,
                    file_symbol=f"file{i}.py::f",
                    description="d",
                )
            )
        return project.id


def test_show_tab_paginates_findings(db):
    project_id = _make_project_with_findings(12)
    update, context, edit = _update_and_context(f"reg:tab:{project_id}:open")

    _run(registry_module.show_tab(update, context))

    args, kwargs = edit.await_args
    assert "12" in args[0]
    assert "стр. 1/2" in args[0]
    assert f"reg:tab:{project_id}:open:1" in _flat_callbacks(kwargs["reply_markup"])


def test_show_tab_second_page_shows_remaining_findings(db):
    project_id = _make_project_with_findings(12)
    update, context, edit = _update_and_context(f"reg:tab:{project_id}:open:1")

    _run(registry_module.show_tab(update, context))

    args, kwargs = edit.await_args
    assert "стр. 2/2" in args[0]


def test_show_tab_project_not_found_uses_wrapped_markup(db):
    update, context, edit = _update_and_context("reg:tab:999:open")

    _run(registry_module.show_tab(update, context))

    args, kwargs = edit.await_args
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)
