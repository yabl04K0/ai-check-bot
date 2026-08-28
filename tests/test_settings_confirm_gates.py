"""⚙️ Настройки — отключение провайдера, удаление ключа/аккаунта теперь
требуют подтверждения вместо мгновенного срабатывания по одному тапу
(см. app/bot/handlers/settings_admin.py: disable_provider/*_yes,
prompt_clear_provider_key/clear_provider_key, prompt_delete_extra_account),
и 🕘 История — постраничный список проектов/запусков."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import settings_admin as settings_module
from app.config import ProviderSettings
from app.db.models import HistoryEntry, Project, ProviderName, TaskType
from app.db.session import get_session
from app.providers.accounts_store import add_extra_account
from app.providers.gemini import GeminiProvider
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _context():
    autocheck = SimpleNamespace(
        enabled=False, full_threshold_pct=10, lite_hours_before_reset=1, lite_threshold_pct=20
    )
    settings = SimpleNamespace(
        admin_tg_id=1, providers=ProviderSettings(gemini_api_key="env-key"), autocheck=autocheck
    )
    gemini = GeminiProvider("env-key")
    registry = ProviderRegistry({ProviderName.GEMINI: gemini})
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": settings,
                "provider_registry": registry,
                "autocheck_enabled_override": False,
            }
        ),
        user_data={},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


def _callback_update(data: str):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1)), query


def _flat_callbacks(kwargs) -> list[str]:
    return [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]


def test_disable_provider_asks_for_confirmation_without_disabling(db):
    context = _context()
    update, query = _callback_update("set:disable:gemini")

    _run(settings_module.disable_provider(update, context))

    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    assert not registry.is_disabled(ProviderName.GEMINI)
    args, kwargs = query.edit_message_text.await_args
    assert "set:disable_yes:gemini" in _flat_callbacks(kwargs)


def test_disable_provider_yes_actually_disables(db):
    context = _context()
    update, query = _callback_update("set:disable_yes:gemini")

    _run(settings_module.disable_provider_yes(update, context))

    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    assert registry.is_disabled(ProviderName.GEMINI)


def test_prompt_clear_provider_key_does_not_clear_yet(db, monkeypatch):
    monkeypatch.setattr(settings_module, "get_key_override", lambda name: "override")
    context = _context()
    update, query = _callback_update("set:key_clear:gemini")

    _run(settings_module.prompt_clear_provider_key(update, context))

    args, kwargs = query.edit_message_text.await_args
    assert "set:key_clear_yes:gemini" in _flat_callbacks(kwargs)


def test_prompt_delete_extra_account_does_not_delete_yet(db):
    add_extra_account(ProviderName.GEMINI, "secret-1")
    from app.providers.accounts_store import list_extra_accounts

    entry = list_extra_accounts(ProviderName.GEMINI)[0]
    context = _context()
    update, query = _callback_update(f"set:key_del:gemini:{entry.id}")

    _run(settings_module.prompt_delete_extra_account(update, context))

    from app.providers.accounts_store import list_extra_accounts as list_again

    assert len(list_again(ProviderName.GEMINI)) == 1  # ничего не удалено на этом шаге
    args, kwargs = query.edit_message_text.await_args
    assert f"set:key_del_yes:gemini:{entry.id}" in _flat_callbacks(kwargs)


def _make_project_with_entries(count: int) -> int:
    with get_session() as session:
        project = Project(name="P", repo_full_name="o/p")
        session.add(project)
        session.flush()
        for _ in range(count):
            session.add(HistoryEntry(project_id=project.id, task_type=TaskType.CHECK_FULL))
        return project.id


def test_show_history_for_project_paginates(db):
    project_id = _make_project_with_entries(12)
    update, query = _callback_update(f"hist:proj:{project_id}")
    context = _context()

    _run(settings_module.show_history_for_project(update, context))

    args, kwargs = query.edit_message_text.await_args
    assert "стр. 1/2" in args[0]
    assert f"hist:proj:{project_id}:1" in _flat_callbacks(kwargs)


def test_show_history_projects_paginates(db):
    with get_session() as session:
        for i in range(10):
            session.add(Project(name=f"P{i}", repo_full_name=f"o/p{i}"))
    update, query = _callback_update("menu:history")
    context = _context()

    _run(settings_module.show_history_projects(update, context))

    args, kwargs = query.edit_message_text.await_args
    assert "стр." in args[0]
    assert "hist:page:1" in _flat_callbacks(kwargs)
