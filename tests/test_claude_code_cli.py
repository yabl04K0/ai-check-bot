"""Claude Code CLI (`claude -p --output-format json`) — исполнение на
подписке Max/Pro вместо метрируемого ANTHROPIC_API_KEY (см.
app.providers.claude.ClaudeProvider). Проверяет: статус без реального
запуска CLI (никогда не тратит деньги на рендер Настроек), выбор между
локальной сессией/явным токеном, разбор JSON-ответа, эвристику квоты и
перебор нескольких аккаунтов при ошибке."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.models import ProviderAccountStatus
from app.providers.base import ProviderError, ProviderNotAuthenticatedError, ProviderQuotaExceededError
from app.providers.claude_code_cli import ClaudeCodeCliProvider


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = ""):
    def _run(args, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return _run


def _json_result(text: str = "привет от клода", input_tokens: int = 5, output_tokens: int = 7) -> str:
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return json.dumps({"is_error": False, "result": text, "usage": usage})


def test_no_cli_path_reports_not_connected():
    provider = ClaudeCodeCliProvider(None)
    assert provider.auth_status().status == ProviderAccountStatus.NOT_CONNECTED


def test_no_cli_path_raises_on_run_prompt():
    provider = ClaudeCodeCliProvider(None)
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_prompt("привет")


def test_no_local_session_and_no_token_reports_not_connected(monkeypatch):
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: False)
    provider = ClaudeCodeCliProvider("claude")
    assert provider.auth_status().status == ProviderAccountStatus.NOT_CONNECTED


def test_local_session_without_token_reports_connected(monkeypatch):
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: True)
    provider = ClaudeCodeCliProvider("claude")
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED
    assert "локальная сессия" in status.detail


def test_explicit_token_reports_connected_without_checking_session(monkeypatch):
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: False)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED
    assert "токен" in status.detail


def test_auth_status_never_invokes_subprocess(monkeypatch):
    """Критично: рендер ⚙️ Настроек не должен тратить реальные деньги на
    подписке — auth_status() обязан быть чисто локальной проверкой."""
    import subprocess

    def _boom(*args, **kwargs):
        raise AssertionError("auth_status() не должен запускать subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: True)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")

    provider.auth_status()  # не должно поднять AssertionError


def test_run_prompt_uses_local_session_env_without_oauth_token(monkeypatch, db):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: True)
    provider = ClaudeCodeCliProvider("claude")

    result = provider.run_prompt("привет")

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured["env"]
    assert result.text == "привет от клода"
    assert result.input_tokens == 5
    assert result.output_tokens == 7


def test_run_prompt_passes_oauth_token_in_env(monkeypatch, db):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token-1")

    provider.run_prompt("привет")

    assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-token-1"


def test_quota_error_in_stderr_raises_quota_exceeded(monkeypatch):
    monkeypatch.setattr(
        "app.providers.claude_code_cli.subprocess.run",
        _fake_run(returncode=1, stderr="Error: rate limit exceeded"),
    )
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_is_error_json_with_quota_marker_raises_quota_exceeded(monkeypatch):
    body = json.dumps({"is_error": True, "result": "usage limit reached for this account"})
    monkeypatch.setattr(
        "app.providers.claude_code_cli.subprocess.run", _fake_run(returncode=0, stdout=body)
    )
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


def test_other_failure_stays_generic_error(monkeypatch):
    monkeypatch.setattr(
        "app.providers.claude_code_cli.subprocess.run",
        _fake_run(returncode=1, stderr="Error: something else broke"),
    )
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")
    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("привет")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)


def test_falls_over_to_extra_account_on_quota_error(monkeypatch, db):
    calls: list[str | None] = []

    def _run(args, **kwargs):
        token = kwargs["env"].get("CLAUDE_CODE_OAUTH_TOKEN")
        calls.append(token)
        if token == "sk-first":
            return SimpleNamespace(returncode=1, stdout="", stderr="rate limit exceeded")
        return SimpleNamespace(returncode=0, stdout=_json_result("успех со второго аккаунта"), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-first", extra_accounts=["sk-second"])

    result = provider.run_prompt("привет")

    assert calls == ["sk-first", "sk-second"]
    assert result.text == "успех со второго аккаунта"


def test_multi_account_status_reports_count(monkeypatch):
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: False)
    provider = ClaudeCodeCliProvider(
        "claude", oauth_token="sk-first", extra_accounts=["sk-second", "sk-third"]
    )
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED
    assert "3" in status.detail
