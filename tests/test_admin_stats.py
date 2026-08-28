"""show_admin строила строку "По провайдерам: " + join(...) or "—" —
из-за приоритета операторов в Python (+ раньше or) `"По провайдерам: " + ""`
даёт непустую строку "По провайдерам: " (с непустым префиксом), которая
сама по себе truthy, так что `or "—"` никогда не срабатывал: при пустом
by_provider юзер видел голое "По провайдерам: " без объяснения, что список
пуст, вместо "По провайдерам: —"."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.settings_admin import dry_run, on_text, prompt_broadcast, show_admin
from app.db.models import User


def _run(coro):
    return asyncio.run(coro)


def _admin_update_context(admin_tg_id: int = 999):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="menu:admin")
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id))
    settings = SimpleNamespace(admin_tg_id=admin_tg_id)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings}))
    return update, context, edit


def test_show_admin_renders_dash_when_no_jobs_have_a_provider(db):
    update, context, edit = _admin_update_context()

    _run(show_admin(update, context))

    edit.assert_awaited_once()
    (text,), _ = edit.await_args
    assert "По провайдерам: —" in text


def _admin_subcreen_context(admin_tg_id: int = 999):
    from app.providers.registry import ProviderRegistry

    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="admin:dry_run")
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id))
    settings = SimpleNamespace(
        admin_tg_id=admin_tg_id, autocheck=SimpleNamespace(enabled=False)
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": settings, "provider_registry": ProviderRegistry({})}
        ),
        user_data={},
    )
    return update, context, edit


def test_dry_run_back_button_returns_to_admin_panel_not_main_menu(db):
    """Раньше nav_row() без аргумента (-> menu:main) выкидывал админа
    мимо панели 👑 Админка сразу на главный экран (см. аудит меню). Теперь
    первая кнопка ряда ("Назад") ведёт на menu:admin — home_button
    ("Меню" -> menu:main) законно остаётся второй кнопкой того же ряда."""
    update, context, edit = _admin_subcreen_context()

    _run(dry_run(update, context))

    args, kwargs = edit.await_args
    back_target = kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert back_target == "menu:admin"


def test_prompt_broadcast_back_button_returns_to_admin_panel(db):
    update, context, edit = _admin_subcreen_context()
    update.callback_query.data = "admin:broadcast"

    _run(prompt_broadcast(update, context))

    args, kwargs = edit.await_args
    back_target = kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert back_target == "menu:admin"


def test_broadcast_on_text_success_gives_way_back_to_admin(db):
    """Раньше итоговое сообщение рассылки уходило без единой кнопки —
    тупиковый экран без пути назад (см. аудит меню)."""
    reply = AsyncMock()
    message = SimpleNamespace(text="привет всем", reply_text=reply)
    update = SimpleNamespace(
        message=message, effective_user=SimpleNamespace(id=999), effective_chat=SimpleNamespace(id=1)
    )
    context = SimpleNamespace(
        user_data={"awaiting": "broadcast"},
        application=SimpleNamespace(bot_data={"settings": SimpleNamespace(admin_tg_id=999)}),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    _run(on_text(update, context))

    reply.assert_awaited_once()
    args, kwargs = reply.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "menu:admin" in callbacks


def test_show_admin_renders_actual_counts_when_present(db):
    from app.db.models import Job, JobStatus, ProviderName, TaskType
    from app.db.session import get_session

    with get_session() as session:
        session.add(User(tg_id=999, display_name="Admin", is_admin=True))
        session.add(
            Job(
                task_type=TaskType.FIX,
                provider=ProviderName.CLAUDE,
                status=JobStatus.DONE,
            )
        )

    update, context, edit = _admin_update_context()

    _run(show_admin(update, context))

    (text,), _ = edit.await_args
    assert "claude=1" in text
    assert "По провайдерам: —" not in text
