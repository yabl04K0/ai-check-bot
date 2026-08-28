"""run_agentic_task — НАСТОЯЩИЙ агентный прогон Claude Code CLI (реальный
доступ к файлам/bash через --permission-mode bypassPermissions), не одна
LLM-реплика вида run_prompt (см. запрос пользователя: "мне нужно что бы
иишка могла запускать агенты на своей же подписке"). Провайдер сам не
проверяет тумблеры автономности/подтверждение — это ответственность
вызывающего кода (app.ai_chat.tools), тут проверяем только сам вызов CLI."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.db.models import AccountPriority, ProviderName
from app.providers.agent_permissions import set_can_push_github
from app.providers.ai_autonomy import set_ai_github_token_access
from app.providers.base import ProviderError, ProviderNotAuthenticatedError
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.providers.tiers import set_tier


def _json_result(text: str = "готово", input_tokens: int = 10, output_tokens: int = 20) -> str:
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return json.dumps({"is_error": False, "result": text, "usage": usage})


def test_no_cli_path_raises():
    provider = ClaudeCodeCliProvider(None)
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_agentic_task("почини баг", "/some/project")


def test_no_configured_account_raises(monkeypatch):
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: False)
    provider = ClaudeCodeCliProvider("claude")
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_agentic_task("почини баг", "/some/project")


def test_run_agentic_task_uses_bypass_permissions_and_project_cwd(monkeypatch, db, tmp_path):
    captured = {}

    def _run(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")

    result = provider.run_agentic_task("почини баг в auth.py", str(tmp_path))

    assert "--permission-mode" in captured["args"]
    assert "bypassPermissions" in captured["args"]
    assert captured["cwd"] == str(tmp_path)
    assert result.text == "готово"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


def test_run_agentic_task_can_edit_false_uses_plan_mode(monkeypatch, db, tmp_path):
    captured = {}

    def _run(args, **kwargs):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")

    provider.run_agentic_task("почитай код", str(tmp_path), can_edit=False)

    idx = captured["args"].index("--permission-mode")
    assert captured["args"][idx + 1] == "plan"


def test_run_agentic_task_github_token_absent_by_default(monkeypatch, db, tmp_path):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    set_ai_github_token_access(True)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token", github_token="ghp_secret")

    provider.run_agentic_task("задача", str(tmp_path))

    assert "GITHUB_TOKEN" not in captured["env"]


def test_run_agentic_task_github_token_included_for_head_tier(monkeypatch, db, tmp_path):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    set_ai_github_token_access(True)
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token", github_token="ghp_secret")

    provider.run_agentic_task("задача", str(tmp_path))

    assert captured["env"]["GITHUB_TOKEN"] == "ghp_secret"


def test_run_agentic_task_github_token_included_with_explicit_override(monkeypatch, db, tmp_path):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    set_ai_github_token_access(True)
    set_can_push_github(ProviderName.CLAUDE_CODE, True)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token", github_token="ghp_secret")

    provider.run_agentic_task("задача", str(tmp_path))

    assert captured["env"]["GITHUB_TOKEN"] == "ghp_secret"


def test_run_agentic_task_respects_forced_account_label(monkeypatch, db, tmp_path):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="primary-token", extra_accounts=["extra-token"])

    provider.run_agentic_task("задача", str(tmp_path), account_label="extra:1")

    assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "extra-token"


def test_run_agentic_task_unknown_account_label_raises(monkeypatch, db, tmp_path):
    provider = ClaudeCodeCliProvider("claude", oauth_token="primary-token")
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_agentic_task("задача", str(tmp_path), account_label="extra:5")


def test_run_agentic_task_raises_on_is_error(monkeypatch, db, tmp_path):
    def _run(args, **kwargs):
        return SimpleNamespace(
            returncode=1, stdout=json.dumps({"is_error": True, "result": "не смог"}), stderr=""
        )

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")

    with pytest.raises(ProviderError, match="не смог"):
        provider.run_agentic_task("задача", str(tmp_path))


def test_run_agentic_task_raises_on_non_json_output(monkeypatch, db, tmp_path):
    def _run(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not json at all", stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")

    with pytest.raises(ProviderError):
        provider.run_agentic_task("задача", str(tmp_path))


def test_run_agentic_task_raises_on_timeout(monkeypatch, db, tmp_path):
    def _run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs["timeout"])

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="sk-token")

    with pytest.raises(ProviderError, match="не уложился"):
        provider.run_agentic_task("задача", str(tmp_path))
