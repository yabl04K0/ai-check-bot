from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TelegramError

import app.scheduler.quota_warnings as quota_warnings_module
from app.db.models import ProviderName
from app.providers.base import QuotaEstimate
from app.providers.tiers import AccountPriority, set_tier
from app.scheduler.quota_warnings import check_and_warn


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_warned_state():
    quota_warnings_module._WARNED.clear()
    yield
    quota_warnings_module._WARNED.clear()


class _FakeRegistry:
    def __init__(self, disabled=None):
        self._disabled = disabled or set()

    def is_disabled(self, name):
        return name in self._disabled


def _make_application(admin_tg_id=12345, disabled=None):
    settings = SimpleNamespace(admin_tg_id=admin_tg_id)
    registry = _FakeRegistry(disabled=disabled)
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(bot_data={"settings": settings, "provider_registry": registry}, bot=bot)


def _fake_estimate(used_pct, hours_to_reset=None, is_estimate=True):
    def _fn(registry, provider, account_label):
        return QuotaEstimate(used_pct=used_pct, hours_to_reset=hours_to_reset, is_estimate=is_estimate)

    return _fn


def test_no_admin_tg_id_sends_no_message(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _fake_estimate(90.0))
    application = _make_application(admin_tg_id=None)

    _run(check_and_warn(application))

    application.bot.send_message.assert_not_awaited()


def test_empty_head_tier_sends_no_message(db, monkeypatch):
    application = _make_application()

    _run(check_and_warn(application))

    application.bot.send_message.assert_not_awaited()


def test_below_threshold_sends_no_message(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _fake_estimate(50.0))
    application = _make_application()

    _run(check_and_warn(application))

    application.bot.send_message.assert_not_awaited()


def test_crossing_threshold_sends_exactly_one_message_with_account_and_percent(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _fake_estimate(90.0))
    application = _make_application()

    _run(check_and_warn(application))

    application.bot.send_message.assert_awaited_once()
    call_args = application.bot.send_message.call_args
    assert call_args[0][0] == 12345
    text = call_args[0][1]
    assert "gemini:primary" in text
    assert "90%" in text


def test_second_tick_while_still_above_threshold_does_not_send_again(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _fake_estimate(90.0))
    application = _make_application()

    _run(check_and_warn(application))
    _run(check_and_warn(application))

    application.bot.send_message.assert_awaited_once()


def test_dropping_below_then_crossing_again_sends_a_new_warning(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    application = _make_application()
    values = iter([90.0, 50.0, 92.0])

    def _fn(registry, provider, account_label):
        return QuotaEstimate(used_pct=next(values), hours_to_reset=None)

    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _fn)

    _run(check_and_warn(application))
    _run(check_and_warn(application))
    _run(check_and_warn(application))

    assert application.bot.send_message.await_count == 2


def test_disabled_provider_is_skipped_and_clears_warned_state(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    quota_warnings_module._WARNED.add((ProviderName.GEMINI, "primary"))

    def _should_not_be_called(registry, provider, account_label):
        raise AssertionError("account_quota_estimate_for must not be called for a disabled provider")

    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _should_not_be_called)
    application = _make_application(disabled={ProviderName.GEMINI})

    _run(check_and_warn(application))

    application.bot.send_message.assert_not_awaited()
    assert (ProviderName.GEMINI, "primary") not in quota_warnings_module._WARNED


def test_claude_code_primary_real_usage_gets_experimental_label(db, monkeypatch):
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)
    monkeypatch.setattr(
        quota_warnings_module, "account_quota_estimate_for", _fake_estimate(90.0, is_estimate=False)
    )
    application = _make_application()

    _run(check_and_warn(application))

    text = application.bot.send_message.call_args[0][1]
    assert "🧪" in text
    assert "неофициальный" in text


def test_telegram_error_from_send_message_is_caught_and_does_not_propagate(db, monkeypatch):
    set_tier(ProviderName.GEMINI, "primary", AccountPriority.HEAD)
    monkeypatch.setattr(quota_warnings_module, "account_quota_estimate_for", _fake_estimate(90.0))
    application = _make_application()
    application.bot.send_message = AsyncMock(side_effect=TelegramError("boom"))

    _run(check_and_warn(application))

    application.bot.send_message.assert_awaited_once()
