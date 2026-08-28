from __future__ import annotations

import pytest

from app.providers.rate_limit_headers import estimate_from_scraped, parse_duration_to_hours, scrape


def test_scrape_picks_known_headers_and_ignores_unknown():
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "250",
        "content-type": "application/json",
        "x-request-id": "abc123",
    }
    result = scrape(headers)
    assert result == {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "250",
    }


def test_scrape_ignores_absent_known_headers():
    headers = {"x-ratelimit-limit-tokens": "1000"}
    result = scrape(headers)
    assert result == {"x-ratelimit-limit-tokens": "1000"}


def test_scrape_returns_empty_dict_when_nothing_matches():
    headers = {"content-type": "application/json"}
    assert scrape(headers) == {}


def test_scrape_picks_anthropic_variant_headers():
    headers = {
        "anthropic-ratelimit-tokens-limit": "500",
        "anthropic-ratelimit-tokens-remaining": "100",
        "anthropic-ratelimit-tokens-reset": "1h",
    }
    result = scrape(headers)
    assert result == headers


def test_scrape_picks_retry_after():
    headers = {"retry-after": "120"}
    assert scrape(headers) == {"retry-after": "120"}


def test_parse_duration_to_hours_bare_seconds():
    assert parse_duration_to_hours("3600") == pytest.approx(1.0)


def test_parse_duration_to_hours_bare_seconds_fractional_result():
    assert parse_duration_to_hours("120") == pytest.approx(120 / 3600)


def test_parse_duration_to_hours_hours_only():
    assert parse_duration_to_hours("1h") == pytest.approx(1.0)


def test_parse_duration_to_hours_minutes_only():
    assert parse_duration_to_hours("30m") == pytest.approx(0.5)


def test_parse_duration_to_hours_seconds_suffix_only():
    assert parse_duration_to_hours("45s") == pytest.approx(45 / 3600)


def test_parse_duration_to_hours_combined_hours_minutes_seconds():
    assert parse_duration_to_hours("1h30m2s") == pytest.approx((3600 + 1800 + 2) / 3600)


def test_parse_duration_to_hours_invalid_letters_returns_none():
    assert parse_duration_to_hours("abc") is None


def test_parse_duration_to_hours_invalid_suffix_returns_none():
    assert parse_duration_to_hours("10x") is None


def test_parse_duration_to_hours_empty_string_returns_none():
    assert parse_duration_to_hours("") is None


def test_parse_duration_to_hours_trailing_number_without_suffix_returns_none():
    assert parse_duration_to_hours("1h30") is None


def test_estimate_from_scraped_openai_style_headers():
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "250",
        "x-ratelimit-reset-tokens": "3600",
    }
    estimate = estimate_from_scraped(headers)
    assert estimate is not None
    assert estimate.used_pct == pytest.approx(75.0)
    assert estimate.hours_to_reset == pytest.approx(1.0)
    assert estimate.is_estimate is False


def test_estimate_from_scraped_anthropic_style_headers():
    headers = {
        "anthropic-ratelimit-tokens-limit": "1000",
        "anthropic-ratelimit-tokens-remaining": "250",
        "anthropic-ratelimit-tokens-reset": "1h",
    }
    estimate = estimate_from_scraped(headers)
    assert estimate is not None
    assert estimate.used_pct == pytest.approx(75.0)
    assert estimate.hours_to_reset == pytest.approx(1.0)
    assert estimate.is_estimate is False


def test_estimate_from_scraped_missing_limit_returns_none():
    headers = {"x-ratelimit-remaining-tokens": "250"}
    assert estimate_from_scraped(headers) is None


def test_estimate_from_scraped_missing_remaining_returns_none():
    headers = {"x-ratelimit-limit-tokens": "1000"}
    assert estimate_from_scraped(headers) is None


def test_estimate_from_scraped_empty_headers_returns_none():
    assert estimate_from_scraped({}) is None


def test_estimate_from_scraped_non_numeric_limit_returns_none():
    headers = {"x-ratelimit-limit-tokens": "not-a-number", "x-ratelimit-remaining-tokens": "250"}
    assert estimate_from_scraped(headers) is None


def test_estimate_from_scraped_non_numeric_remaining_returns_none():
    headers = {"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "not-a-number"}
    assert estimate_from_scraped(headers) is None


def test_estimate_from_scraped_zero_limit_returns_none():
    headers = {"x-ratelimit-limit-tokens": "0", "x-ratelimit-remaining-tokens": "0"}
    assert estimate_from_scraped(headers) is None


def test_estimate_from_scraped_negative_limit_returns_none():
    headers = {"x-ratelimit-limit-tokens": "-5", "x-ratelimit-remaining-tokens": "0"}
    assert estimate_from_scraped(headers) is None


def test_estimate_from_scraped_reset_header_absent_leaves_hours_to_reset_none():
    headers = {"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "250"}
    estimate = estimate_from_scraped(headers)
    assert estimate is not None
    assert estimate.hours_to_reset is None
    assert estimate.used_pct == pytest.approx(75.0)


def test_estimate_from_scraped_falls_back_to_retry_after_for_reset():
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "500",
        "retry-after": "120",
    }
    estimate = estimate_from_scraped(headers)
    assert estimate is not None
    assert estimate.hours_to_reset == pytest.approx(120 / 3600)


def test_estimate_from_scraped_used_pct_computation():
    headers = {"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "250"}
    estimate = estimate_from_scraped(headers)
    assert estimate is not None
    assert estimate.used_pct == 75.0
