"""UI-флоу настройки Project.nightly_check_time (см. app/scheduler/autocheck.py::_nightly_tick):
экран-промпт, валидация HH:MM, сохранение, отключение."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import projects as projects_module
from app.db.models import Project
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _callback_update(data: str, user_data=None):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1))
    context = SimpleNamespace(user_data=user_data if user_data is not None else {})
    return update, context, edit


def _text_update(text: str, user_data=None):
    reply = AsyncMock()
    message = SimpleNamespace(text=text, reply_text=reply)
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(user_data=user_data if user_data is not None else {})
    return update, context, reply


def _add_project(name="demo", repo="o/demo") -> int:
    with get_session() as session:
        project = Project(name=name, repo_full_name=repo)
        session.add(project)
        session.flush()
        return project.id


def test_prompt_nightly_check_time_sets_awaiting(db):
    project_id = _add_project()
    update, context, edit = _callback_update(f"proj:nightly:{project_id}")

    _run(projects_module.prompt_nightly_check_time(update, context))

    assert context.user_data["awaiting"] == f"nightly_check_time:{project_id}"
    edit.assert_awaited_once()


def test_valid_time_saves_and_confirms(db):
    project_id = _add_project()
    update, context, reply = _text_update(
        "03:30", user_data={"awaiting": f"nightly_check_time:{project_id}"}
    )

    _run(projects_module.on_text(update, context))

    with get_session() as session:
        project = session.get(Project, project_id)
        assert project.nightly_check_time == "03:30"
    assert context.user_data["awaiting"] is None
    reply.assert_awaited_once()
    (text,), _kwargs = reply.await_args
    assert "03:30" in text


def test_invalid_time_is_rejected_and_not_saved(db):
    project_id = _add_project()
    update, context, reply = _text_update(
        "not-a-time", user_data={"awaiting": f"nightly_check_time:{project_id}"}
    )

    _run(projects_module.on_text(update, context))

    with get_session() as session:
        project = session.get(Project, project_id)
        assert project.nightly_check_time is None
    assert context.user_data["awaiting"] == f"nightly_check_time:{project_id}"
    reply.assert_awaited_once()
    (text,), _kwargs = reply.await_args
    assert "формат" in text.lower()


def test_clear_nightly_check_time(db):
    project_id = _add_project()
    with get_session() as session:
        project = session.get(Project, project_id)
        project.nightly_check_time = "03:30"
        project.nightly_last_run_date = "2026-08-28"

    update, context, edit = _callback_update(f"proj:nightly_clear:{project_id}")

    _run(projects_module.clear_nightly_check_time(update, context))

    with get_session() as session:
        project = session.get(Project, project_id)
        assert project.nightly_check_time is None
        assert project.nightly_last_run_date is None
    edit.assert_awaited_once()
