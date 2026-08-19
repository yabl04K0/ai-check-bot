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

from app.bot.handlers.settings_admin import show_admin
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
