"""HANDOVER-паттерн (см. app.tasks.pipeline) срабатывает только если
провайдер реально бросает ProviderQuotaExceededError на 429/лимите —
раньше это исключение существовало только на бумаге, ни один провайдер
его не поднимал."""

from __future__ import annotations

import anthropic
import httpx
import pytest

from app.providers.base import ProviderError, ProviderQuotaExceededError
from app.providers.claude import ClaudeProvider
from app.providers.codex import CodexProvider
from app.providers.cursor import CursorProvider, _looks_like_quota_error
from app.providers.local_llm import LocalLLMProvider


class _RaisingMessages:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **kwargs):
        raise self._exc


class _FakeAnthropicClient:
    def __init__(self, exc: Exception) -> None:
        self.messages = _RaisingMessages(exc)


def _fake_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code, request=request)


def test_claude_rate_limit_raises_quota_exceeded():
    provider = ClaudeProvider("sk-ant-test")
    exc = anthropic.RateLimitError("rate limited", response=_fake_response(429), body=None)
    provider._client = _FakeAnthropicClient(exc)

    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_claude_overloaded_raises_quota_exceeded():
    provider = ClaudeProvider("sk-ant-test")
    exc = anthropic.APIStatusError("overloaded", response=_fake_response(529), body=None)
    provider._client = _FakeAnthropicClient(exc)

    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_claude_other_status_error_stays_generic():
    provider = ClaudeProvider("sk-ant-test")
    exc = anthropic.APIStatusError("server error", response=_fake_response(500), body=None)
    provider._client = _FakeAnthropicClient(exc)

    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("привет")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)


def _fake_post_returning(status_code: int, json_body: dict | None = None):
    def _fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, json=json_body or {}, request=request)

    return _fake_post


def test_codex_429_raises_quota_exceeded(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post_returning(429, {"error": "rate limited"}))
    provider = CodexProvider("sk-test")
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_codex_other_error_stays_generic(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post_returning(500, {"error": "boom"}))
    provider = CodexProvider("sk-test")
    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("привет")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)


def test_local_llm_429_raises_quota_exceeded(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post_returning(429))
    provider = LocalLLMProvider("http://localhost:11434/v1", "qwen2.5-coder:14b")
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_local_llm_connection_error_stays_generic(monkeypatch):
    def _raise_connect_error(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _raise_connect_error)
    provider = LocalLLMProvider("http://localhost:11434/v1", "qwen2.5-coder:14b")
    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("привет")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)


def test_cursor_quota_marker_detection():
    assert _looks_like_quota_error("Error: rate limit exceeded, try again later") is True
    assert _looks_like_quota_error("HTTP 429 Too Many Requests") is True
    assert _looks_like_quota_error("weekly quota reached for this account") is True
    assert _looks_like_quota_error("command not found") is False


def test_cursor_run_prompt_raises_quota_on_rate_limit_message(tmp_path):
    script = tmp_path / "cursor-agent"
    script.write_text("#!/bin/sh\necho 'Error: rate limit exceeded' >&2\nexit 1\n")
    script.chmod(0o755)

    provider = CursorProvider(str(script))
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_cursor_run_prompt_raises_generic_on_other_failure(tmp_path):
    script = tmp_path / "cursor-agent"
    script.write_text("#!/bin/sh\necho 'Error: file not found' >&2\nexit 1\n")
    script.chmod(0o755)

    provider = CursorProvider(str(script))
    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("привет")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)
