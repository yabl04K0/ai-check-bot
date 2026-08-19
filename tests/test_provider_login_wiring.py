from __future__ import annotations

from app.db.models import ProviderAccountStatus
from app.providers.codex import CodexProvider
from app.providers.cursor import CursorProvider


def test_cursor_supports_login_only_with_cli_path():
    assert CursorProvider(None).supports_login() is False
    assert CursorProvider("/usr/local/bin/cursor-agent").supports_login() is True


def test_cursor_login_uses_configured_cli(tmp_path):
    script = tmp_path / "cursor-agent"
    script.write_text("#!/bin/sh\necho ok\nexit 0\n")
    script.chmod(0o755)

    result = CursorProvider(str(script)).login()
    assert result.success is True


def test_codex_prefers_api_key_over_cli_for_auth_status():
    provider = CodexProvider("sk-test", cli_path="/usr/local/bin/codex")
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED


def test_codex_without_api_key_but_with_cli_reports_not_connected_with_hint():
    provider = CodexProvider(None, cli_path="/usr/local/bin/codex")
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.NOT_CONNECTED
    assert "Войти" in status.detail
    assert provider.supports_login() is True


def test_codex_without_api_key_or_cli_does_not_support_login():
    provider = CodexProvider(None)
    assert provider.supports_login() is False
