"""⚙️ Настройки → 🎚 Приоритеты аккаунтов — глобальный тумблер режима
делегации + тап-цикл тира на конкретном аккаунте (см. запрос пользователя:
"хочу задавать некоторые акки как акки для делегации работы, должна быть
кнопка что бы включить этот режим")."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.settings_admin import (
    cycle_account_tier,
    show_tiers,
    toggle_delegation_mode,
    toggle_delegation_mode_yes,
)
from app.db.models import AccountPriority, ProviderName
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.providers.registry import ProviderRegistry
from app.providers.tiers import delegation_mode_enabled, get_tier


def _run(coro):
    return asyncio.run(coro)


def _update(data: str, admin_tg_id: int = 1):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id)), query


def _context():
    registry = ProviderRegistry(
        {ProviderName.CLAUDE_CODE: ClaudeCodeCliProvider("claude", oauth_token="tok")}
    )
    return SimpleNamespace(application=SimpleNamespace(bot_data={"provider_registry": registry}))


def _button_labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_show_tiers_reports_disabled_by_default(db):
    update, query = _update("set:tiers")
    _run(show_tiers(update, _context()))

    (text,), kwargs = query.edit_message_text.await_args
    markup = kwargs["reply_markup"]
    assert "выключено" in text
    labels = _button_labels(markup)
    assert any("claude_code:primary" in label for label in labels)
    assert any("➖" in label for label in labels)


def test_toggle_delegation_mode_flips_and_persists(db):
    update, query = _update("set:tiers:toggle")
    _run(toggle_delegation_mode(update, _context()))

    assert delegation_mode_enabled() is True
    (text,), kwargs = query.edit_message_text.await_args
    assert "включено" in text


def test_toggle_delegation_mode_when_enabled_asks_for_confirmation_first(db):
    """Выключение — через confirm_row, как disable_provider (структурно
    то же "отключение"), а не мгновенно одним тапом (см. аудит меню)."""
    from app.providers.tiers import set_delegation_mode

    set_delegation_mode(True)
    update, query = _update("set:tiers:toggle")
    _run(toggle_delegation_mode(update, _context()))

    assert delegation_mode_enabled() is True
    args, kwargs = query.edit_message_text.await_args
    assert "?" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "set:tiers:toggle_yes" in callbacks


def test_toggle_delegation_mode_yes_actually_disables(db):
    from app.providers.tiers import set_delegation_mode

    set_delegation_mode(True)
    update, query = _update("set:tiers:toggle_yes")
    _run(toggle_delegation_mode_yes(update, _context()))

    assert delegation_mode_enabled() is False


def test_cycle_account_tier_advances_through_all_three_then_unset(db):
    update, query = _update("set:tier_cycle:claude_code:primary")

    _run(cycle_account_tier(update, _context()))
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") == AccountPriority.HEAD

    _run(cycle_account_tier(update, _context()))
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") == AccountPriority.MEDIUM

    _run(cycle_account_tier(update, _context()))
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") == AccountPriority.DELEGATION

    _run(cycle_account_tier(update, _context()))
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") is None


def test_cycle_account_tier_handles_extra_label_with_colon(db):
    update, query = _update("set:tier_cycle:claude_code:extra:1")
    _run(cycle_account_tier(update, _context()))
    assert get_tier(ProviderName.CLAUDE_CODE, "extra:1") == AccountPriority.HEAD
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") is None


def test_cycle_account_tier_shows_icon_after_assignment(db):
    update, query = _update("set:tier_cycle:claude_code:primary")
    _run(cycle_account_tier(update, _context()))

    (text,), kwargs = query.edit_message_text.await_args
    labels = _button_labels(kwargs["reply_markup"])
    assert any("👑" in label and "Глава" in label for label in labels)


def test_cycle_account_tier_answers_with_new_tier_name(db):
    """Раньше отвечал пустым answer() — промах мимо нужного тира можно
    было заметить только докрутив круг заново (см. аудит меню)."""
    update, query = _update("set:tier_cycle:claude_code:primary")
    _run(cycle_account_tier(update, _context()))

    update.callback_query.answer.assert_awaited_once_with("→ Глава")
