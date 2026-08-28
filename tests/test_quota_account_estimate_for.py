from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db.models import ProviderName, QuotaUsageLog
from app.db.session import get_session
from app.providers import quota as quota_module
from app.providers.base import QuotaEstimate
from app.providers.quota import account_quota_estimate_for


def _log(provider, account_label, input_tokens, output_tokens, hours_ago=1):
    with get_session() as session:
        session.add(
            QuotaUsageLog(
                provider=provider,
                account_label=account_label,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                ts=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            )
        )


class _FakeRegistry:
    def __init__(self, provider):
        self._provider = provider

    def get(self, name):
        return self._provider


def test_prefers_live_rate_limit_data_when_present_and_parseable(db):
    provider = SimpleNamespace(
        _last_rate_limit={"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "250"}
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 75.0
    assert estimate.is_estimate is False


def test_prefers_live_data_over_db_estimate_even_when_db_has_usage(db):
    _log(ProviderName.GEMINI, "primary", 9000, 0)
    provider = SimpleNamespace(
        _last_rate_limit={"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "900"},
        _quota_tracker=SimpleNamespace(weekly_token_budget=10000),
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == pytest.approx(10.0)
    assert estimate.is_estimate is False


def test_falls_back_to_db_estimate_when_provider_has_no_last_rate_limit_attribute(db):
    _log(ProviderName.GEMINI, "primary", 2000, 0)
    provider = SimpleNamespace(_quota_tracker=SimpleNamespace(weekly_token_budget=10000))
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 20.0
    assert estimate.is_estimate is True


def test_falls_back_to_db_estimate_when_last_rate_limit_is_empty(db):
    _log(ProviderName.GEMINI, "primary", 2000, 0)
    provider = SimpleNamespace(
        _last_rate_limit={}, _quota_tracker=SimpleNamespace(weekly_token_budget=10000)
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 20.0
    assert estimate.is_estimate is True


def test_falls_back_to_db_estimate_when_last_rate_limit_missing_remaining_header(db):
    _log(ProviderName.GEMINI, "primary", 2000, 0)
    provider = SimpleNamespace(
        _last_rate_limit={"x-ratelimit-limit-tokens": "1000"},
        _quota_tracker=SimpleNamespace(weekly_token_budget=10000),
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 20.0
    assert estimate.is_estimate is True


def test_falls_back_to_db_estimate_when_last_rate_limit_headers_are_non_numeric(db):
    _log(ProviderName.GEMINI, "primary", 2000, 0)
    provider = SimpleNamespace(
        _last_rate_limit={
            "x-ratelimit-limit-tokens": "not-a-number",
            "x-ratelimit-remaining-tokens": "250",
        },
        _quota_tracker=SimpleNamespace(weekly_token_budget=10000),
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 20.0
    assert estimate.is_estimate is True


def test_falls_back_to_db_estimate_when_last_rate_limit_limit_is_zero(db):
    _log(ProviderName.GEMINI, "primary", 2000, 0)
    provider = SimpleNamespace(
        _last_rate_limit={"x-ratelimit-limit-tokens": "0", "x-ratelimit-remaining-tokens": "0"},
        _quota_tracker=SimpleNamespace(weekly_token_budget=10000),
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 20.0
    assert estimate.is_estimate is True


def test_falls_back_correctly_when_provider_has_neither_attribute(db):
    provider = SimpleNamespace()
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct is None
    assert estimate.hours_to_reset is None


def test_falls_back_correctly_when_quota_tracker_has_no_weekly_token_budget(db):
    provider = SimpleNamespace(_quota_tracker=SimpleNamespace(weekly_token_budget=None))
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct is None


def test_falls_back_correctly_when_quota_tracker_attribute_itself_missing_budget_field(db):
    provider = SimpleNamespace(_quota_tracker=SimpleNamespace())
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct is None


def test_claude_code_primary_prefers_real_usage_endpoint(db, monkeypatch):
    real = QuotaEstimate(used_pct=42.0, hours_to_reset=3.0, is_estimate=False)
    monkeypatch.setattr(quota_module, "fetch_claude_code_primary_usage", lambda cli_path: real)
    provider = SimpleNamespace(_cli_path="claude")
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.CLAUDE_CODE, "primary")

    assert estimate is real


def test_claude_code_extra_account_does_not_use_real_usage_endpoint(db, monkeypatch):
    def _should_not_be_called(cli_path):
        raise AssertionError("real usage endpoint must only be tried for the primary account")

    monkeypatch.setattr(quota_module, "fetch_claude_code_primary_usage", _should_not_be_called)
    provider = SimpleNamespace(_quota_tracker=SimpleNamespace(weekly_token_budget=None))
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.CLAUDE_CODE, "extra:1")

    assert estimate.used_pct is None


def test_claude_code_primary_falls_back_when_real_usage_unavailable(db, monkeypatch):
    _log(ProviderName.CLAUDE_CODE, "primary", 2000, 0)
    monkeypatch.setattr(quota_module, "fetch_claude_code_primary_usage", lambda cli_path: None)
    provider = SimpleNamespace(
        _cli_path="claude", _quota_tracker=SimpleNamespace(weekly_token_budget=10000)
    )
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.CLAUDE_CODE, "primary")

    assert estimate.used_pct == 20.0
    assert estimate.is_estimate is True


def test_db_fallback_is_scoped_to_the_requested_account_label(db):
    _log(ProviderName.GEMINI, "primary", 1000, 0)
    _log(ProviderName.GEMINI, "extra:1", 9000, 0)
    provider = SimpleNamespace(_quota_tracker=SimpleNamespace(weekly_token_budget=10000))
    registry = _FakeRegistry(provider)

    estimate = account_quota_estimate_for(registry, ProviderName.GEMINI, "primary")

    assert estimate.used_pct == 10.0
