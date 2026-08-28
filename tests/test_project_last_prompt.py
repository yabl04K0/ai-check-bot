"""📁 Проект → 📝 Last Prompt: просмотр/редактирование LAST_PROMPT.md —
единственный слот "продолжи отсюда" между сессиями ИИ (см.
app/registry_store/last_prompt.py, app/tasks/project_context.py::gather_last_prompt)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.projects import on_text, prompt_edit_last_prompt, show_last_prompt
from app.db.models import Project
from app.db.session import get_session
from app.registry_store.last_prompt import read_last_prompt


def _run(coro):
    return asyncio.run(coro)


def _make_project(local_path=None) -> int:
    with get_session() as session:
        project = Project(
            name="P", repo_full_name="owner/p", local_path=str(local_path) if local_path else None
        )
        session.add(project)
        session.flush()
        return project.id


def _update_and_context(project_id: int, user_data=None):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=f"proj:lastprompt:{project_id}")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data=user_data or {})
    return update, context, edit


def test_show_last_prompt_without_local_path(db):
    project_id = _make_project()
    update, context, edit = _update_and_context(project_id)

    _run(show_last_prompt(update, context))

    args, kwargs = edit.await_args
    assert "local_path" in args[0]


def test_show_last_prompt_empty_when_no_file(tmp_path, db):
    project_id = _make_project(tmp_path)
    update, context, edit = _update_and_context(project_id)

    _run(show_last_prompt(update, context))

    args, kwargs = edit.await_args
    assert "(пусто)" in args[0]


def test_show_last_prompt_reads_existing_content(tmp_path, db):
    (tmp_path / "LAST_PROMPT.md").write_text("Продолжи с шага 3\n", encoding="utf-8")
    project_id = _make_project(tmp_path)
    update, context, edit = _update_and_context(project_id)

    _run(show_last_prompt(update, context))

    args, kwargs = edit.await_args
    assert "Продолжи с шага 3" in args[0]


def test_prompt_edit_blocks_without_local_path(db):
    project_id = _make_project()
    update, context, edit = _update_and_context(project_id)

    _run(prompt_edit_last_prompt(update, context))

    assert "awaiting" not in context.user_data
    args, kwargs = edit.await_args
    assert "local_path" in args[0]


def test_prompt_edit_sets_awaiting(tmp_path, db):
    project_id = _make_project(tmp_path)
    update, context, edit = _update_and_context(project_id)

    _run(prompt_edit_last_prompt(update, context))

    assert context.user_data["awaiting"] == f"last_prompt:{project_id}"


def test_on_text_writes_last_prompt(tmp_path, db):
    project_id = _make_project(tmp_path)
    reply = AsyncMock()
    message = SimpleNamespace(text="Продолжи с шага 3", reply_text=reply)
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(user_data={"awaiting": f"last_prompt:{project_id}"})

    _run(on_text(update, context))

    assert read_last_prompt(tmp_path) == "Продолжи с шага 3"
    assert context.user_data["awaiting"] is None
