from __future__ import annotations

import subprocess

import pytest

from app.providers.base import ProviderError
from app.providers.cli_login import run_cli_login


def test_missing_cli_path_raises_provider_error():
    with pytest.raises(ProviderError):
        run_cli_login(None, missing_path_hint="настрой CURSOR_AGENT_CLI_PATH")


def test_successful_login(tmp_path):
    script = tmp_path / "fake-cli.sh"
    script.write_text("#!/bin/sh\necho 'logged in as demo@example.com'\nexit 0\n")
    script.chmod(0o755)

    result = run_cli_login(str(script), missing_path_hint="unused")

    assert result.success is True
    assert "demo@example.com" in result.message


def test_failed_login_returns_message_not_exception(tmp_path):
    script = tmp_path / "fake-cli.sh"
    script.write_text("#!/bin/sh\necho 'auth failed' >&2\nexit 1\n")
    script.chmod(0o755)

    result = run_cli_login(str(script), missing_path_hint="unused")

    assert result.success is False
    assert "auth failed" in result.message


def test_timeout_returns_captured_partial_output(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="fake login", timeout=1, output="visit https://example.com/device")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_cli_login("/usr/bin/does-not-matter", missing_path_hint="unused")

    assert result.success is False
    assert "example.com/device" in result.message
    assert "браузер" in result.message
