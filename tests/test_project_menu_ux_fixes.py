"""Фиксы из аудита меню для app/bot/handlers/projects.py:
1. show_projects/manage_project сбрасывают awaiting (уход через ◀️ Назад
   раньше оставлял его висеть — следующее свободное сообщение в ЛЮБОМ
   другом месте бота, например в 🗨 ИИ-чате, молча перехватывалось on_text).
2. Карточка проекта (_project_settings_menu) даёт nav_row (🏠 Меню одним
   тапом), toggle_autocheck/toggle_self_check не теряют local_path/self-check
   из текста и корректно перерисовывают "проект не найден".
3. ADD_PROJECT_PROMPT рендерится с parse_mode="Markdown" (раньше показывал
   буквальные бэктики на первом экране добавления проекта).
4. Успешное добавление проекта ИЗ визарда выбора проектов (chk:*) теперь
   возвращает на project_multiselect с сохранённым выбором, а не на
   самостоятельный экран 📁 Проекты."""

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


def _add_project(name="demo", repo="o/demo", local_path=None) -> int:
    with get_session() as session:
        project = Project(name=name, repo_full_name=repo, local_path=local_path)
        session.add(project)
        session.flush()
        return project.id


def test_show_projects_resets_stale_awaiting(db):
    update, context, edit = _callback_update("menu:projects", user_data={"awaiting": "add_project"})

    _run(projects_module.show_projects(update, context))

    assert context.user_data.get("awaiting") is None


def test_manage_project_resets_stale_awaiting(db):
    project_id = _add_project()
    update, context, edit = _callback_update(
        f"proj:manage:{project_id}", user_data={"awaiting": "last_prompt:999"}
    )

    _run(projects_module.manage_project(update, context))

    assert context.user_data.get("awaiting") is None


def test_project_card_gives_menu_button(db):
    project_id = _add_project()
    update, context, edit = _callback_update(f"proj:manage:{project_id}")

    _run(projects_module.manage_project(update, context))

    args, kwargs = edit.await_args
    markup = kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "menu:main" in callbacks


def test_toggle_autocheck_keeps_full_project_info(db):
    project_id = _add_project(local_path="/repo/demo")
    update, context, edit = _callback_update(f"proj:toggle_auto:{project_id}")

    _run(projects_module.toggle_autocheck(update, context))

    args, kwargs = edit.await_args
    assert "/repo/demo" in args[0]
    assert "self-check" in args[0]


def test_toggle_autocheck_project_not_found_rerenders_with_nav(db):
    update, context, edit = _callback_update("proj:toggle_auto:999999")

    _run(projects_module.toggle_autocheck(update, context))

    edit.assert_awaited_once()
    args, kwargs = edit.await_args
    assert "не найден" in args[0]
    markup = kwargs["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "menu:projects" in callbacks


def test_prompt_add_project_manual_uses_markdown(db):
    update, context, edit = _callback_update("proj:add:manual")

    _run(projects_module.prompt_add_project_manual(update, context))

    args, kwargs = edit.await_args
    assert kwargs.get("parse_mode") == "Markdown"


def test_add_project_success_returns_to_wizard_when_flow_active(db):
    """Кнопка "➕ Добавить проект" внутри мультивыбора визарда ЧЕК/Фичи/...
    раньше уводила на самостоятельный экран 📁 Проекты, теряя отмеченные
    flow['selected'] без возможности вернуться к их выбору."""
    from app.db.models import TaskType

    reply = AsyncMock()
    message = SimpleNamespace(text="Новый проект; o/new", reply_text=reply)
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(
        user_data={"awaiting": "add_project", "flow": {"task_type": TaskType.FIX, "selected": {1, 2}}}
    )

    _run(projects_module.on_text(update, context))

    with get_session() as session:
        assert session.query(Project).filter_by(repo_full_name="o/new").count() == 1

    reply.assert_awaited_once()
    (text,), kwargs = reply.await_args
    assert "мультивыбор" in text
    assert kwargs["reply_markup"] is not None


def test_add_project_success_without_flow_returns_to_projects_list(db):
    reply = AsyncMock()
    message = SimpleNamespace(text="Другой проект; o/other", reply_text=reply)
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(user_data={"awaiting": "add_project"})

    _run(projects_module.on_text(update, context))

    (text,), kwargs = reply.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "menu:projects" in callbacks
