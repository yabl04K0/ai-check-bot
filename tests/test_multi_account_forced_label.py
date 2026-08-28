"""run_with_account_fallback(forced_account_label=...) — приоритеты
аккаунтов (app.providers.tiers) должны направлять вызов ИМЕННО в
указанный аккаунт, а не позволять ему тихо съехать на первый живой."""

from __future__ import annotations

import pytest

from app.providers.base import ProviderNotAuthenticatedError, ProviderResult
from app.providers.multi_account import run_with_account_fallback


def test_forced_label_picks_only_matching_account():
    calls = []

    def attempt(label, credential):
        calls.append(label)
        return ProviderResult(text=f"from {label}")

    result = run_with_account_fallback(
        [("primary", "a"), ("extra:1", "b"), ("extra:2", "c")],
        attempt,
        not_configured_hint="none",
        forced_account_label="extra:1",
    )

    assert calls == ["extra:1"]
    assert result.text == "from extra:1"


def test_forced_label_not_found_raises_not_configured():
    def attempt(label, credential):
        raise AssertionError("не должно вызываться")

    with pytest.raises(ProviderNotAuthenticatedError):
        run_with_account_fallback(
            [("primary", "a")],
            attempt,
            not_configured_hint="нет такого аккаунта",
            forced_account_label="extra:5",
        )


def test_forced_label_does_not_fall_back_to_other_accounts_on_error():
    """Тир "делегация" не должен тихо съехать на аккаунт тира "глава",
    даже если у форсированного аккаунта ошибка — по умолчанию (без
    forced_account_label) fallback происходит, с ним — нет."""
    from app.providers.base import ProviderError

    calls = []

    def attempt(label, credential):
        calls.append(label)
        raise ProviderError(f"{label} упал")

    with pytest.raises(ProviderError):
        run_with_account_fallback(
            [("primary", "a"), ("extra:1", "b")],
            attempt,
            not_configured_hint="none",
            forced_account_label="extra:1",
        )

    assert calls == ["extra:1"]  # "primary" не тронут


def test_none_forced_label_keeps_old_behavior_tries_all():
    calls = []

    def attempt(label, credential):
        calls.append(label)
        from app.providers.base import ProviderError

        if label == "primary":
            raise ProviderError("primary недоступен")
        return ProviderResult(text="ok")

    result = run_with_account_fallback(
        [("primary", "a"), ("extra:1", "b")], attempt, not_configured_hint="none"
    )

    assert calls == ["primary", "extra:1"]
    assert result.text == "ok"
