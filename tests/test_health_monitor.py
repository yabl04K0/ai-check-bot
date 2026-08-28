from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.scheduler.health_monitor as health_monitor_module
from app.db.models import ProviderAccountStatus, ProviderName
from app.providers import circuit_breaker
from app.providers.base import AuthStatus, ProviderError, ProviderResult
from app.scheduler.health_monitor import check_and_notify


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_broken_state():
    health_monitor_module._BROKEN.clear()
    yield
    health_monitor_module._BROKEN.clear()


class _FakeProvider:
    def __init__(self, name: ProviderName, ok: bool = True) -> None:
        self.name = name
        self.ok = ok
        self.calls: list[str] = []

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None) -> ProviderResult:
        self.calls.append(prompt)
        if not self.ok:
            raise ProviderError("down")
        return ProviderResult(text="pong")


class _FakeRegistry:
    def __init__(self, providers, disabled=frozenset()):
        self._providers = providers
        self._disabled = disabled

    def all(self):
        return dict(self._providers)

    def get(self, name):
        return self._providers[name]

    def is_disabled(self, name):
        return name in self._disabled


def _make_application(admin_tg_id=12345, providers=None, disabled=frozenset()):
    settings = SimpleNamespace(admin_tg_id=admin_tg_id)
    registry = _FakeRegistry(providers or {}, disabled=disabled)
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(bot_data={"settings": settings, "provider_registry": registry}, bot=bot)


def test_no_admin_tg_id_sends_no_message_and_probes_nothing(db):
    provider = _FakeProvider(ProviderName.GEMINI, ok=True)
    application = _make_application(admin_tg_id=None, providers={ProviderName.GEMINI: provider})

    _run(check_and_notify(application))

    application.bot.send_message.assert_not_awaited()
    assert provider.calls == []


def test_healthy_account_sends_no_message(db):
    provider = _FakeProvider(ProviderName.GEMINI, ok=True)
    application = _make_application(providers={ProviderName.GEMINI: provider})

    _run(check_and_notify(application))

    application.bot.send_message.assert_not_awaited()
    assert provider.calls == ["ping"]


def test_broken_account_sends_exactly_one_message(db):
    provider = _FakeProvider(ProviderName.GEMINI, ok=False)
    application = _make_application(providers={ProviderName.GEMINI: provider})

    _run(check_and_notify(application))

    application.bot.send_message.assert_awaited_once()
    text = application.bot.send_message.call_args[0][1]
    assert "gemini:primary" in text
    assert "не отвечает" in text


def test_second_tick_while_still_broken_does_not_resend(db):
    provider = _FakeProvider(ProviderName.GEMINI, ok=False)
    application = _make_application(providers={ProviderName.GEMINI: provider})

    _run(check_and_notify(application))
    _run(check_and_notify(application))

    application.bot.send_message.assert_awaited_once()


def test_recovery_sends_a_recovered_message(db):
    provider = _FakeProvider(ProviderName.GEMINI, ok=False)
    application = _make_application(providers={ProviderName.GEMINI: provider})
    _run(check_and_notify(application))
    application.bot.send_message.reset_mock()

    provider.ok = True
    _run(check_and_notify(application))

    application.bot.send_message.assert_awaited_once()
    text = application.bot.send_message.call_args[0][1]
    assert "снова работает" in text


def test_disabled_provider_is_not_probed(db):
    provider = _FakeProvider(ProviderName.GEMINI, ok=True)
    application = _make_application(
        providers={ProviderName.GEMINI: provider}, disabled=frozenset({ProviderName.GEMINI})
    )

    _run(check_and_notify(application))

    assert provider.calls == []
    application.bot.send_message.assert_not_awaited()


def test_cli_provider_is_never_actively_probed(db):
    provider = _FakeProvider(ProviderName.CLAUDE_CODE, ok=True)
    application = _make_application(providers={ProviderName.CLAUDE_CODE: provider})

    _run(check_and_notify(application))

    assert provider.calls == []


def test_cli_provider_broken_via_real_circuit_breaker_trip_sends_message(db):
    provider = _FakeProvider(ProviderName.CLAUDE_CODE, ok=True)
    application = _make_application(providers={ProviderName.CLAUDE_CODE: provider})
    circuit_breaker.record_failure(ProviderName.CLAUDE_CODE, "primary")

    _run(check_and_notify(application))

    application.bot.send_message.assert_awaited_once()
    text = application.bot.send_message.call_args[0][1]
    assert "claude_code:primary" in text
    assert provider.calls == []


def test_cli_provider_cooldown_expiry_does_not_send_a_false_recovery_message(db, monkeypatch):
    provider = _FakeProvider(ProviderName.CLAUDE_CODE, ok=True)
    application = _make_application(providers={ProviderName.CLAUDE_CODE: provider})
    circuit_breaker.record_failure(ProviderName.CLAUDE_CODE, "primary")
    _run(check_and_notify(application))
    application.bot.send_message.reset_mock()

    monkeypatch.setattr(circuit_breaker.time, "monotonic", lambda: 10_000.0)
    _run(check_and_notify(application))

    application.bot.send_message.assert_not_awaited()
