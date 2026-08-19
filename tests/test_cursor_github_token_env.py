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

from app.providers.ai_autonomy import set_ai_github_token_access
from app.providers.cursor import CursorProvider


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


def test_github_token_included_when_toggle_on_and_token_provided(db, monkeypatch):
    captured = _capture_env(monkeypatch)
    set_ai_github_token_access(True)
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
