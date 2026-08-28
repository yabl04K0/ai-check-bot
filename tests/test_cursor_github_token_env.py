"""CursorProvider.run_prompt() раньше вообще не передавал `env=` в
subprocess.run — cursor-agent молча наследовал ПОЛНОЕ окружение процесса
бота, включая GITHUB_TOKEN, ANTHROPIC_API_KEY и т.д., без какого-либо
контроля. Теперь GITHUB_TOKEN явно включается в окружение CLI-агента
только когда включён тумблер app.providers.ai_autonomy (⚙️ Настройки →
Автономность ИИ), а по умолчанию явно вычищается из копии os.environ,
даже если он есть в реальном окружении бота."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

from app.db.models import AccountPriority, ProviderName
from app.providers.agent_permissions import set_can_push_github
from app.providers.ai_autonomy import set_ai_github_token_access
from app.providers.cursor import CursorProvider
from app.providers.tiers import set_tier


def _capture_env(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_github_token_absent_by_default_even_if_in_process_env(db, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_leaked_if_bug_present")
    captured = _capture_env(monkeypatch)

    provider = CursorProvider("cursor-agent", github_token="ghp_leaked_if_bug_present")
    provider.run_prompt("hello")

    assert "GITHUB_TOKEN" not in captured["env"]


def test_github_token_absent_when_toggle_off_even_if_provider_has_token(db, monkeypatch):
    captured = _capture_env(monkeypatch)
    provider = CursorProvider("cursor-agent", github_token="ghp_secret")

    provider.run_prompt("hello")

    assert "GITHUB_TOKEN" not in captured["env"]


def test_github_token_included_when_toggle_on_and_provider_explicitly_allowed(db, monkeypatch):
    captured = _capture_env(monkeypatch)
    set_ai_github_token_access(True)
    set_can_push_github(ProviderName.CURSOR, True)
    provider = CursorProvider("cursor-agent", github_token="ghp_secret")

    provider.run_prompt("hello")

    assert captured["env"]["GITHUB_TOKEN"] == "ghp_secret"


def test_github_token_absent_by_default_for_non_head_provider(db, monkeypatch):
    """По умолчанию (без явного разрешения и без тира 'Глава') push
    запрещён — не главная нейронка не может пушить в GitHub."""
    captured = _capture_env(monkeypatch)
    set_ai_github_token_access(True)
    provider = CursorProvider("cursor-agent", github_token="ghp_secret")

    provider.run_prompt("hello")

    assert "GITHUB_TOKEN" not in captured["env"]


def test_github_token_included_for_head_tier_provider_without_explicit_toggle(db, monkeypatch):
    """Главная нейронка (тир 'Глава') получает push-доступ по умолчанию,
    без ручного включения per-провайдерного тумблера."""
    captured = _capture_env(monkeypatch)
    set_ai_github_token_access(True)
    set_tier(ProviderName.CURSOR, "primary", AccountPriority.HEAD)
    provider = CursorProvider("cursor-agent", github_token="ghp_secret")

    provider.run_prompt("hello")

    assert captured["env"]["GITHUB_TOKEN"] == "ghp_secret"


def test_github_token_absent_when_toggle_on_but_provider_has_no_token(db, monkeypatch):
    captured = _capture_env(monkeypatch)
    set_ai_github_token_access(True)
    provider = CursorProvider("cursor-agent", github_token=None)

    provider.run_prompt("hello")

    assert "GITHUB_TOKEN" not in captured["env"]


def test_other_env_vars_still_inherited(db, monkeypatch):
    """Маскируем только GITHUB_TOKEN — остальное окружение (PATH и т.п.)
    должно доехать до CLI как обычно, иначе cursor-agent сам не найдётся
    в PATH при реальном запуске."""
    captured = _capture_env(monkeypatch)
    provider = CursorProvider("cursor-agent")

    provider.run_prompt("hello")

    assert captured["env"]["PATH"] == os.environ["PATH"]
