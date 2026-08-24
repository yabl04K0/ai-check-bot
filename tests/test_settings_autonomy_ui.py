"""⚙️ Настройки → тумблеры автономности ИИ: включение требует отдельного
экрана с дисклеймером (не должно включаться первым же тапом), выключение
— мгновенное, без вопросов (возврат к безопасному состоянию не требует
подтверждения)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import InlineKeyboardMarkup

from app.bot.handlers.settings_admin import (
    confirm_auto_approve,
    confirm_token_access,
    show_settings,
    toggle_auto_approve,
    toggle_token_access,
)
from app.providers.ai_autonomy import (
    ai_command_auto_approve_enabled,
    ai_github_token_access_enabled,
    set_ai_command_auto_approve,
    set_ai_github_token_access,
)
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _context(admin_tg_id: int = 1):
    settings = SimpleNamespace(
        admin_tg_id=admin_tg_id, autocheck=SimpleNamespace(enabled=False, full_threshold_pct=60,
                                                             lite_hours_before_reset=1, lite_threshold_pct=90)
    )
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": settings, "provider_registry": ProviderRegistry({})}
        )
    )


def _update(admin_tg_id: int = 1):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="")
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id)), query


def test_show_settings_passes_markup_as_keyword_not_positional(db):
    """Регрессия: edit_message_text(*_settings_view(context)) распаковывало
    (text, markup) в ДВА позиционных аргумента, а реальная сигнатура
    telegram.CallbackQuery.edit_message_text — (text, parse_mode,
    reply_markup, ...), так что markup улетал в parse_mode. В реальном боте
    это валилось BadRequest("Unsupported parse_mode") при любом открытии
    ⚙️ Настройки; AsyncMock() без spec это не ловит, поэтому проверяем
    форму вызова явно."""
    update, query = _update()
    context = _context()

    _run(show_settings(update, context))

    args, kwargs = query.edit_message_text.await_args
    assert len(args) == 1
    assert isinstance(args[0], str)
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


def test_first_tap_on_token_access_shows_disclaimer_without_enabling(db):
    update, query = _update()
    context = _context()

    _run(toggle_token_access(update, context))

    assert ai_github_token_access_enabled() is False
    (text,), kwargs = query.edit_message_text.await_args
    assert "Дисклеймер" in text
    assert "GITHUB_TOKEN" in text


def test_confirm_after_disclaimer_actually_enables(db):
    update, query = _update()
    context = _context()

    _run(confirm_token_access(update, context))

    assert ai_github_token_access_enabled() is True


def test_toggling_off_is_immediate_no_disclaimer(db):
    set_ai_github_token_access(True)
    update, query = _update()
    context = _context()

    _run(toggle_token_access(update, context))

    assert ai_github_token_access_enabled() is False
    args, kwargs = query.edit_message_text.await_args
    assert "Дисклеймер" not in args[0]


def test_first_tap_on_auto_approve_shows_disclaimer_without_enabling(db):
    update, query = _update()
    context = _context()

    _run(toggle_auto_approve(update, context))

    assert ai_command_auto_approve_enabled() is False
    (text,), kwargs = query.edit_message_text.await_args
    assert "Дисклеймер" in text


def test_confirm_auto_approve_enables(db):
    update, query = _update()
    context = _context()

    _run(confirm_auto_approve(update, context))

    assert ai_command_auto_approve_enabled() is True


def test_toggling_auto_approve_off_is_immediate(db):
    set_ai_command_auto_approve(True)
    update, query = _update()
    context = _context()

    _run(toggle_auto_approve(update, context))

    assert ai_command_auto_approve_enabled() is False
