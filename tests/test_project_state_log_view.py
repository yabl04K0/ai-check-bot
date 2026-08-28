"""📁 Проект → 📒 STATE_LOG: просмотр хвоста STATE_LOG.md (только на
чтение — бот пишет туда сам через HANDOVER, см. app/tasks/handover.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.projects import show_state_log
from app.db.models import Project
from app.db.session import get_session
from app.registry_store.state_log import append_entry


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


def _update_and_context(project_id: int):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=f"proj:statelog:{project_id}")
    update = SimpleNamespace(callback_query=query)
    return update, SimpleNamespace(user_data={}), edit


def test_show_state_log_without_local_path(db):
    project_id = _make_project()
    update, context, edit = _update_and_context(project_id)

    _run(show_state_log(update, context))

    args, kwargs = edit.await_args
    assert "local_path" in args[0]


def test_show_state_log_empty_when_no_file(tmp_path, db):
    project_id = _make_project(tmp_path)
    update, context, edit = _update_and_context(project_id)

    _run(show_state_log(update, context))

    args, kwargs = edit.await_args
    assert "записей ещё нет" in args[0]


def test_show_state_log_shows_recent_entries(tmp_path, db):
    append_entry(tmp_path, "FIX", {"area": "auth"})
    project_id = _make_project(tmp_path)
    update, context, edit = _update_and_context(project_id)

    _run(show_state_log(update, context))

    args, kwargs = edit.await_args
    assert "[FIX]" in args[0]
    assert "area: auth" in args[0]
