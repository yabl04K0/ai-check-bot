"""push_commit() раньше просто звал `git push origin branch` и полагался
на то, что вызывающий код где-то настроит аутентификацию — но никто
этого не делал (см. app/bot/handlers/check.py::commit_yes), так что
автопуш реально сработал бы только если у хоста уже были настроены
git-креды для конкретного репо вне бота. Теперь GITHUB_TOKEN передаётся
через окружение процесса (не argv — виден только тому же
пользователю/root, не всем через `ps aux`)."""

from __future__ import annotations

import base64
import subprocess
from unittest.mock import MagicMock

import pytest

from app.github_integration.client import GitHubClient, GitHubError


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_push_commit_sends_token_via_env_not_argv(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = GitHubClient("ghp_supersecret")
    client.push_commit(tmp_path, branch="main")

    # токена нет в самой команде (argv), которую видно через `ps aux`
    assert not any("ghp_supersecret" in part for part in captured["cmd"])
    assert captured["cmd"] == ["git", "push", "origin", "main"]

    env = captured["env"]
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    expected_b64 = base64.b64encode(b"x-access-token:ghp_supersecret").decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected_b64}"


def test_push_commit_actually_pushes_to_a_real_remote(tmp_path):
    """Функциональная проверка: локальный bare-репо как remote, реальный
    git push (http.extraheader тут не задействуется — file:// его
    игнорирует, но сам механизм push/commit проверяется по-настоящему)."""
    remote_path = tmp_path / "remote.git"
    remote_path.mkdir()
    _git("init", "--bare", "-q", cwd=remote_path)

    work_path = tmp_path / "work"
    work_path.mkdir()
    _git("init", "-q", "-b", "main", cwd=work_path)
    _git("config", "user.email", "t@example.com", cwd=work_path)
    _git("config", "user.name", "T", cwd=work_path)
    _git("remote", "add", "origin", str(remote_path), cwd=work_path)
    (work_path / "f.txt").write_text("hello\n")
    _git("add", "f.txt", cwd=work_path)
    _git("commit", "-q", "-m", "initial", cwd=work_path)

    client = GitHubClient("fake-token-not-used-for-local-remote")
    output = client.push_commit(work_path, branch="main")

    # у bare-репо HEAD по умолчанию смотрит на master, которого нет — нам
    # запушили main, поэтому смотрим лог именно этой ветки, не HEAD
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s", "main"],
        cwd=remote_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "initial"
    assert isinstance(output, str)


def test_push_commit_failure_raises_github_error(tmp_path):
    # нет ни git-репозитория, ни remote — push обязан упасть предсказуемо
    with pytest.raises(GitHubError):
        GitHubClient("fake-token").push_commit(tmp_path, branch="main")
