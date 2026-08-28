from __future__ import annotations

import httpx
import pytest

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.accounts_store import add_extra_account
from app.providers.base import (
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderQuotaExceededError,
    RunOptions,
)
from app.providers.custom_api import (
    CustomOpenAICompatibleProvider,
    clear_config,
    detect_provider_name,
    get_config,
    known_account_labels,
    set_auth_style,
    set_config,
    set_response_format,
)
from app.providers.quota import account_usage_summary


def _openai_response(status_code=200, text="ok", model="resp-model", headers=None):
    def _post(url, **kwargs):
        request = httpx.Request("POST", url, headers=kwargs.get("headers"))
        body = {
            "model": model,
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        return httpx.Response(status_code, json=body, headers=headers or {}, request=request)

    return _post


def _anthropic_response(status_code=200, blocks=None, model="resp-model", headers=None):
    def _post(url, **kwargs):
        request = httpx.Request("POST", url, headers=kwargs.get("headers"))
        body = {
            "model": model,
            "content": blocks if blocks is not None else [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 3, "output_tokens": 9},
        }
        return httpx.Response(status_code, json=body, headers=headers or {}, request=request)

    return _post


def _error_response(status_code, headers=None):
    def _post(url, **kwargs):
        request = httpx.Request("POST", url, headers=kwargs.get("headers"))
        return httpx.Response(status_code, json={"error": "boom"}, headers=headers or {}, request=request)

    return _post


def _configure(
    account_label="primary",
    display_name="MyAPI",
    base_url="https://api.example.com",
    model="my-model",
    auth_style="bearer",
    response_format="openai",
):
    set_config(
        account_label,
        display_name=display_name,
        base_url=base_url,
        model=model,
        auth_style=auth_style,
        response_format=response_format,
    )


def test_get_config_returns_unconfigured_defaults_when_unset(db):
    config = get_config("primary")
    assert config.display_name is None
    assert config.base_url is None
    assert config.model is None
    assert config.auth_style == "bearer"
    assert config.response_format == "openai"
    assert config.is_configured is False


def test_set_config_persists_all_fields_and_round_trips(db):
    _configure(auth_style="x-api-key", response_format="anthropic")
    config = get_config("primary")
    assert config.display_name == "MyAPI"
    assert config.base_url == "https://api.example.com"
    assert config.model == "my-model"
    assert config.auth_style == "x-api-key"
    assert config.response_format == "anthropic"
    assert config.is_configured is True


def test_set_config_strips_trailing_slash_from_base_url(db):
    _configure(base_url="https://api.example.com/")
    assert get_config("primary").base_url == "https://api.example.com"


def test_set_config_invalid_auth_style_raises_value_error(db):
    with pytest.raises(ValueError):
        _configure(auth_style="totally-invalid")


def test_set_config_invalid_response_format_raises_value_error(db):
    with pytest.raises(ValueError):
        _configure(response_format="totally-invalid")


def test_set_config_defaults_auth_style_and_response_format_when_omitted(db):
    set_config("primary", display_name="X", base_url="https://x.com", model="m")
    config = get_config("primary")
    assert config.auth_style == "bearer"
    assert config.response_format == "openai"


def test_set_auth_style_updates_only_auth_style(db):
    _configure()
    set_auth_style("primary", "none")
    config = get_config("primary")
    assert config.auth_style == "none"
    assert config.display_name == "MyAPI"
    assert config.base_url == "https://api.example.com"


def test_set_auth_style_invalid_raises_value_error(db):
    _configure()
    with pytest.raises(ValueError):
        set_auth_style("primary", "invalid")


def test_set_response_format_updates_only_response_format(db):
    _configure()
    set_response_format("primary", "anthropic")
    config = get_config("primary")
    assert config.response_format == "anthropic"
    assert config.model == "my-model"


def test_set_response_format_invalid_raises_value_error(db):
    _configure()
    with pytest.raises(ValueError):
        set_response_format("primary", "invalid")


def test_clear_config_removes_all_fields_back_to_defaults(db):
    _configure()
    clear_config("primary")
    config = get_config("primary")
    assert config.is_configured is False
    assert config.auth_style == "bearer"
    assert config.response_format == "openai"


def test_accounts_with_different_labels_are_independent(db):
    _configure(account_label="primary", display_name="One")
    _configure(account_label="extra:1", display_name="Two")

    assert get_config("primary").display_name == "One"
    assert get_config("extra:1").display_name == "Two"
    assert get_config("extra:2").is_configured is False


def test_known_account_labels_is_just_primary_when_no_extra_accounts(db):
    assert known_account_labels() == ["primary"]


def test_known_account_labels_lists_extras_in_order(db):
    add_extra_account(ProviderName.CUSTOM, "key-a")
    add_extra_account(ProviderName.CUSTOM, "key-b")
    add_extra_account(ProviderName.CUSTOM, "key-c")

    assert known_account_labels() == ["primary", "extra:1", "extra:2", "extra:3"]


def test_known_account_labels_ignores_other_providers_credentials(db):
    add_extra_account(ProviderName.GEMINI, "unrelated-key")
    assert known_account_labels() == ["primary"]


def test_detect_provider_name_uses_owned_by_from_models_endpoint(db, monkeypatch):
    def _get(url, **kwargs):
        assert url == "https://api.example.com/models"
        request = httpx.Request("GET", url, headers=kwargs.get("headers"))
        body = {"data": [{"owned_by": "some-vendor"}]}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "get", _get)

    assert detect_provider_name("https://api.example.com") == "Some Vendor"


def test_detect_provider_name_falls_back_to_host_on_network_error(db, monkeypatch):
    def _get(url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _get)

    assert detect_provider_name("https://api.groq.com/openai/v1") == "Groq"


def test_detect_provider_name_falls_back_to_host_on_malformed_json(db, monkeypatch):
    def _get(url, **kwargs):
        request = httpx.Request("GET", url, headers=kwargs.get("headers"))
        return httpx.Response(200, content=b"not json", request=request)

    monkeypatch.setattr(httpx, "get", _get)

    assert detect_provider_name("https://api.together.xyz") == "Together"


def test_detect_provider_name_falls_back_to_host_on_404(db, monkeypatch):
    def _get(url, **kwargs):
        request = httpx.Request("GET", url, headers=kwargs.get("headers"))
        return httpx.Response(404, json={"error": "not found"}, request=request)

    monkeypatch.setattr(httpx, "get", _get)

    assert detect_provider_name("https://api.mistral.ai/v1") == "Mistral"


def test_detect_provider_name_returns_none_for_unparseable_url(db, monkeypatch):
    def _get(url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _get)

    assert detect_provider_name("not-a-valid-url") is None


def test_auth_status_not_connected_when_not_configured(db):
    provider = CustomOpenAICompatibleProvider(None)
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.NOT_CONNECTED
    assert "ни один аккаунт не настроен" in status.detail


def test_auth_status_connected_when_configured_with_api_key(db):
    _configure()
    provider = CustomOpenAICompatibleProvider("key1")
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED
    assert status.detail == "MyAPI"


def test_auth_status_shows_account_count_when_multiple_configured_accounts(db):
    add_extra_account(ProviderName.CUSTOM, "key2")
    add_extra_account(ProviderName.CUSTOM, "key3")
    _configure(account_label="primary", display_name="One")
    _configure(account_label="extra:1", display_name="Two")
    _configure(account_label="extra:2", display_name="Three")
    provider = CustomOpenAICompatibleProvider("key1", extra_accounts=["key2", "key3"])
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED
    assert status.detail == "3 аккаунта(ов)"


def test_auth_status_not_connected_when_configured_but_no_key(db):
    _configure()
    provider = CustomOpenAICompatibleProvider(None)
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.NOT_CONNECTED
    assert status.detail == "ключ не задан"


def test_auth_status_connected_when_auth_style_none_even_without_key(db):
    _configure(auth_style="none")
    provider = CustomOpenAICompatibleProvider(None)
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED


def test_run_prompt_raises_not_authenticated_when_not_configured(db):
    provider = CustomOpenAICompatibleProvider("key1")
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_prompt("hi")


def test_run_prompt_openai_format_builds_body_and_parses_response(db, monkeypatch):
    _configure(response_format="openai", auth_style="bearer")
    captured = {}

    def _post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        body = {
            "model": "resp-model",
            "choices": [{"message": {"content": "hello there"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key1")

    result = provider.run_prompt("question", RunOptions(system="be nice", max_tokens=111, temperature=0.5))

    assert result.text == "hello there"
    assert result.model == "resp-model"
    assert result.input_tokens == 5
    assert result.output_tokens == 7
    assert captured["url"] == "https://api.example.com/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer key1"}
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "question"},
    ]
    assert captured["json"]["max_tokens"] == 111
    assert captured["json"]["temperature"] == 0.5


def test_run_prompt_openai_format_without_system_option_skips_system_message(db, monkeypatch):
    _configure(response_format="openai")
    captured = {}

    def _post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        body = {
            "model": "m",
            "choices": [{"message": {"content": "hi"}}],
            "usage": {},
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key1")

    provider.run_prompt("question")

    assert captured["json"]["messages"] == [{"role": "user", "content": "question"}]


def test_run_prompt_anthropic_format_builds_body_and_parses_response(db, monkeypatch):
    _configure(response_format="anthropic", auth_style="x-api-key")
    captured = {}

    def _post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        body = {
            "model": "resp-model",
            "content": [
                {"type": "text", "text": "part one "},
                {"type": "tool_use", "text": "ignored"},
                {"type": "text", "text": "part two"},
            ],
            "usage": {"input_tokens": 3, "output_tokens": 9},
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key2")

    result = provider.run_prompt("question", RunOptions(system="sys prompt"))

    assert result.text == "part one part two"
    assert result.input_tokens == 3
    assert result.output_tokens == 9
    assert captured["url"] == "https://api.example.com/messages"
    assert captured["headers"] == {"x-api-key": "key2", "anthropic-version": "2023-06-01"}
    assert captured["json"]["system"] == "sys prompt"
    assert captured["json"]["messages"] == [{"role": "user", "content": "question"}]


def test_run_prompt_anthropic_format_defaults_system_to_empty_string(db, monkeypatch):
    _configure(response_format="anthropic")
    captured = {}

    def _post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        body = {"model": "m", "content": [{"type": "text", "text": "ok"}], "usage": {}}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key1")

    provider.run_prompt("question")

    assert captured["json"]["system"] == ""


def test_endpoint_url_not_duplicated_when_base_url_already_has_full_path(db, monkeypatch):
    _configure(base_url="https://api.example.com/v1/chat/completions", response_format="openai")
    captured = {}

    def _post(url, **kwargs):
        captured["url"] = url
        request = httpx.Request("POST", url)
        body = {"model": "m", "choices": [{"message": {"content": "ok"}}], "usage": {}}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key1")

    provider.run_prompt("question")

    assert captured["url"] == "https://api.example.com/v1/chat/completions"


def test_run_prompt_with_auth_style_none_sends_no_auth_headers(db, monkeypatch):
    _configure(auth_style="none")
    captured = {}

    def _post(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        request = httpx.Request("POST", url)
        body = {"model": "m", "choices": [{"message": {"content": "ok"}}], "usage": {}}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider(None)

    result = provider.run_prompt("question")

    assert result.text == "ok"
    assert captured["headers"] == {}


def test_run_prompt_with_forced_account_label_uses_exactly_that_account(db, monkeypatch):
    _configure(account_label="primary", display_name="Primary", base_url="https://primary.example.com")
    _configure(account_label="extra:1", display_name="ExtraOne", base_url="https://extra1.example.com")
    _configure(account_label="extra:2", display_name="ExtraTwo", base_url="https://extra2.example.com")
    captured = {}

    def _post(url, **kwargs):
        if url != "https://extra2.example.com/chat/completions":
            raise AssertionError(f"unexpected account hit: {url}")
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        request = httpx.Request("POST", url)
        body = {"model": "m", "choices": [{"message": {"content": "extra two result"}}], "usage": {}}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("primary-key", extra_accounts=["extra1-key", "extra2-key"])

    result = provider.run_prompt("question", RunOptions(forced_account_label="extra:2"))

    assert result.text == "extra two result"
    assert captured["url"] == "https://extra2.example.com/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer extra2-key"}


def test_run_prompt_without_forced_account_label_uses_primary_only(db, monkeypatch):
    _configure(account_label="primary", display_name="Primary", base_url="https://primary.example.com")
    _configure(account_label="extra:1", display_name="ExtraOne", base_url="https://extra1.example.com")
    captured = {}

    def _post(url, **kwargs):
        captured["url"] = url
        request = httpx.Request("POST", url)
        body = {"model": "m", "choices": [{"message": {"content": "primary result"}}], "usage": {}}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("primary-key", extra_accounts=["extra1-key"])

    result = provider.run_prompt("question")

    assert result.text == "primary result"
    assert captured["url"] == "https://primary.example.com/chat/completions"


def test_run_prompt_without_forced_account_label_does_not_fall_back_on_error(db, monkeypatch):
    _configure(account_label="primary", display_name="Primary", base_url="https://primary.example.com")
    _configure(account_label="extra:1", display_name="ExtraOne", base_url="https://extra1.example.com")
    calls = []

    def _post(url, **kwargs):
        calls.append(url)
        if url == "https://primary.example.com/chat/completions":
            request = httpx.Request("POST", url)
            return httpx.Response(500, json={"error": "boom"}, request=request)
        raise AssertionError(f"extra account must not be contacted: {url}")

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("primary-key", extra_accounts=["extra1-key"])

    with pytest.raises(ProviderError):
        provider.run_prompt("question")

    assert calls == ["https://primary.example.com/chat/completions"]


def test_run_prompt_supports_more_than_three_accounts(db, monkeypatch):
    _configure(account_label="extra:5", display_name="Fifth", base_url="https://fifth.example.com")
    captured = {}

    def _post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        request = httpx.Request("POST", url)
        body = {"model": "m", "choices": [{"message": {"content": "fifth result"}}], "usage": {}}
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider(
        "primary-key", extra_accounts=["k1", "k2", "k3", "k4", "k5"]
    )

    result = provider.run_prompt("question", RunOptions(forced_account_label="extra:5"))

    assert result.text == "fifth result"
    assert captured["headers"] == {"Authorization": "Bearer k5"}


def test_run_prompt_raises_not_authenticated_when_configured_but_no_credentials(db):
    _configure(auth_style="bearer")
    provider = CustomOpenAICompatibleProvider(None)
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_prompt("question")


def test_run_prompt_429_raises_quota_exceeded(db, monkeypatch):
    _configure()
    monkeypatch.setattr(httpx, "post", _error_response(429))
    provider = CustomOpenAICompatibleProvider("key1")
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("question")


def test_run_prompt_other_status_error_raises_generic_provider_error(db, monkeypatch):
    _configure()
    monkeypatch.setattr(httpx, "post", _error_response(500))
    provider = CustomOpenAICompatibleProvider("key1")
    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("question")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)


def test_run_prompt_network_error_raises_provider_error(db, monkeypatch):
    _configure()

    def _post(url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key1")
    with pytest.raises(ProviderError):
        provider.run_prompt("question")


def test_run_prompt_malformed_response_raises_provider_error(db, monkeypatch):
    _configure()

    def _post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"unexpected": "shape"}, request=request)

    monkeypatch.setattr(httpx, "post", _post)
    provider = CustomOpenAICompatibleProvider("key1")
    with pytest.raises(ProviderError):
        provider.run_prompt("question")


def test_rate_limit_headers_scraped_from_successful_response(db, monkeypatch):
    _configure()
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "250",
        "x-ratelimit-reset-tokens": "3600",
        "content-type": "application/json",
    }
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")

    provider.run_prompt("question")

    assert provider._last_rate_limit["x-ratelimit-limit-tokens"] == "1000"
    assert provider._last_rate_limit["x-ratelimit-remaining-tokens"] == "250"
    assert "content-type" not in provider._last_rate_limit


def test_rate_limit_headers_scraped_from_error_response(db, monkeypatch):
    _configure()
    headers = {"x-ratelimit-limit-tokens": "500", "x-ratelimit-remaining-tokens": "0"}
    monkeypatch.setattr(httpx, "post", _error_response(429, headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")

    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("question")

    assert provider._last_rate_limit["x-ratelimit-limit-tokens"] == "500"
    assert provider._last_rate_limit["x-ratelimit-remaining-tokens"] == "0"


def test_rate_limit_headers_preserved_when_new_response_has_none(db, monkeypatch):
    _configure()
    headers = {"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "250"}
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")
    assert provider._last_rate_limit["x-ratelimit-limit-tokens"] == "1000"

    monkeypatch.setattr(httpx, "post", _openai_response(headers={}))
    provider.run_prompt("question")

    assert provider._last_rate_limit["x-ratelimit-limit-tokens"] == "1000"


def test_estimate_quota_prefers_real_header_data_when_present(db, monkeypatch):
    _configure()
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "250",
        "x-ratelimit-reset-tokens": "3600",
    }
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.is_estimate is False
    assert estimate.used_pct == pytest.approx(75.0)
    assert estimate.hours_to_reset == pytest.approx(1.0)


def test_estimate_quota_prefers_anthropic_header_names_when_openai_absent(db, monkeypatch):
    _configure(response_format="anthropic", auth_style="x-api-key")
    headers = {
        "anthropic-ratelimit-tokens-limit": "200",
        "anthropic-ratelimit-tokens-remaining": "50",
    }
    monkeypatch.setattr(httpx, "post", _anthropic_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.is_estimate is False
    assert estimate.used_pct == pytest.approx(75.0)
    assert estimate.hours_to_reset is None


def test_estimate_quota_falls_back_to_quota_tracker_when_no_headers_seen(db):
    _configure()
    provider = CustomOpenAICompatibleProvider("key1")
    estimate = provider.estimate_quota()
    assert estimate.used_pct is None
    assert estimate.hours_to_reset is None
    assert estimate.is_estimate is True


def test_estimate_quota_zero_limit_falls_back_to_quota_tracker(db, monkeypatch):
    _configure()
    headers = {"x-ratelimit-limit-tokens": "0", "x-ratelimit-remaining-tokens": "0"}
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.is_estimate is True


def test_estimate_quota_invalid_numeric_headers_falls_back_to_quota_tracker(db, monkeypatch):
    _configure()
    headers = {"x-ratelimit-limit-tokens": "not-a-number", "x-ratelimit-remaining-tokens": "250"}
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.is_estimate is True


def test_estimate_quota_reset_duration_falls_back_to_retry_after_when_reset_tokens_missing(db, monkeypatch):
    _configure()
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "500",
        "retry-after": "120",
    }
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.hours_to_reset == pytest.approx(120 / 3600)


def test_estimate_quota_reset_duration_parses_letter_suffixed_string(db, monkeypatch):
    _configure()
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "500",
        "x-ratelimit-reset-tokens": "1h30m",
    }
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.hours_to_reset == pytest.approx(1.5)


def test_estimate_quota_reset_duration_invalid_suffix_returns_none_but_keeps_used_pct(db, monkeypatch):
    _configure()
    headers = {
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "500",
        "x-ratelimit-reset-tokens": "10x",
    }
    monkeypatch.setattr(httpx, "post", _openai_response(headers=headers))
    provider = CustomOpenAICompatibleProvider("key1")
    provider.run_prompt("question")

    estimate = provider.estimate_quota()

    assert estimate.is_estimate is False
    assert estimate.used_pct == pytest.approx(50.0)
    assert estimate.hours_to_reset is None


def test_quota_usage_recorded_after_successful_run(db, monkeypatch):
    _configure()
    monkeypatch.setattr(httpx, "post", _openai_response())
    provider = CustomOpenAICompatibleProvider("key1")

    provider.run_prompt("question")

    summary = account_usage_summary(ProviderName.CUSTOM)
    assert summary["primary"] == (12, 12)


def test_supports_key_entry_is_true(db):
    provider = CustomOpenAICompatibleProvider(None)
    assert provider.supports_key_entry() is True


def test_update_api_key_changes_live_key(db):
    _configure()
    provider = CustomOpenAICompatibleProvider(None)
    assert provider.auth_status().status == ProviderAccountStatus.NOT_CONNECTED

    provider.update_api_key("fresh-key")

    assert provider.auth_status().status == ProviderAccountStatus.CONNECTED


def test_set_extra_accounts_replaces_list(db):
    provider = CustomOpenAICompatibleProvider("key1", extra_accounts=["a"])
    provider.set_extra_accounts(["b", "c"])
    assert provider._extra_accounts == ["b", "c"]
