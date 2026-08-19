from __future__ import annotations

import pytest

from app.config import load_settings


def test_load_settings_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("ADMIN_TG_ID", "42")
    monkeypatch.setenv("AUTOCHECK_ENABLED", "true")
    monkeypatch.setenv("AUTOCHECK_FULL_THRESHOLD_PCT", "55")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    settings = load_settings(env_file=tmp_path / "does-not-exist.env")

    assert settings.bot_token == "123:abc"
    assert settings.admin_tg_id == 42
    assert settings.autocheck.enabled is True
    assert settings.autocheck.full_threshold_pct == 55
    assert settings.providers.anthropic_api_key == "sk-ant-test"


def test_load_settings_defaults_when_unset(monkeypatch, tmp_path):
    for key in ("BOT_TOKEN", "ADMIN_TG_ID", "AUTOCHECK_ENABLED", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings(env_file=tmp_path / "does-not-exist.env")

    assert settings.bot_token is None
    assert settings.admin_tg_id is None
    assert settings.autocheck.enabled is False
    assert settings.autocheck.full_threshold_pct == 60


def test_require_bot_token_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    settings = load_settings(env_file=tmp_path / "does-not-exist.env")
    with pytest.raises(RuntimeError):
        settings.require_bot_token()
