"""📁 Проекты — удаление требует подтверждения (не срабатывает по одному
тапу), список проектов постраничный (см. app/bot/handlers/projects.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import InlineKeyboardMarkup

from app.bot.handlers import projects as projects_module
from app.db.models import Project
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _update_and_context(callback_data: str):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=callback_data)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1))
    context = SimpleNamespace(user_data={})
    return update, context, edit


def _flat_callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_prompt_delete_project_asks_for_confirmation(db):
    with get_session() as session:
        session.add(Project(name="MyRepo", repo_full_name="o/r"))
        session.flush()
        project_id = session.query(Project).one().id

    update, context, edit = _update_and_context(f"proj:del:{project_id}")

    _run(projects_module.prompt_delete_project(update, context))

    args, kwargs = edit.await_args
    assert "MyRepo" in args[0]
    callbacks = _flat_callbacks(kwargs["reply_markup"])
    assert f"proj:del_yes:{project_id}" in callbacks
    with get_session() as session:
        assert session.query(Project).count() == 1  # ничего не удалено на этом шаге


def test_prompt_delete_project_cancel_routes_back_to_manage_screen(db):
    """Кнопка отмены — proj:manage:<id>, обрабатывается существующим
    manage_project (переиспользование хендлера, не новый код)."""
    with get_session() as session:
        session.add(Project(name="MyRepo", repo_full_name="o/r"))
        session.flush()
        project_id = session.query(Project).one().id

    update, context, edit = _update_and_context(f"proj:del:{project_id}")
    _run(projects_module.prompt_delete_project(update, context))
    args, kwargs = edit.await_args
    callbacks = _flat_callbacks(kwargs["reply_markup"])
    assert f"proj:manage:{project_id}" in callbacks


def test_delete_project_yes_actually_deletes(db):
    with get_session() as session:
        session.add(Project(name="MyRepo", repo_full_name="o/r"))
        session.flush()
        project_id = session.query(Project).one().id

    update, context, edit = _update_and_context(f"proj:del_yes:{project_id}")

    _run(projects_module.delete_project(update, context))

    with get_session() as session:
        assert session.query(Project).count() == 0


def test_show_projects_paginates_beyond_page_size(db):
    with get_session() as session:
        for i in range(10):
            session.add(Project(name=f"P{i}", repo_full_name=f"o/p{i}"))

    update, context, edit = _update_and_context("menu:projects")

    _run(projects_module.show_projects(update, context))

    args, kwargs = edit.await_args
    assert "стр." in args[0]
    assert "proj:page:1" in _flat_callbacks(kwargs["reply_markup"])


def test_show_projects_page_shows_second_page(db):
    with get_session() as session:
        for i in range(10):
            session.add(Project(name=f"P{i}", repo_full_name=f"o/p{i}"))

    update, context, edit = _update_and_context("proj:page:1")

    _run(projects_module.show_projects_page(update, context))

    args, kwargs = edit.await_args
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any("P9" in label for label in labels)
