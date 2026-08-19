"""📤 Запушить (без ИИ) — прямой git commit+push без единого вызова ИИ-
провайдера, отдельно от пайплайна/commit_yes. Работает и для self-check
проектов: это человек жмёт кнопку сам, safety-правило "self-check не
пушит автоматически" (см. app/bot/handlers/check.py) про АВТОМАТИЧЕСКИЙ
пуш ИИ, не про этот ручной путь — собственно, это и есть тот самый
"запушь вручную", который self-check раньше только советовал текстом."""

from __future__ import annotations

import subprocess

import app.bot.handlers.projects as projects_module
from app.bot.handlers.projects import _manual_push_blocking
from app.db.models import Project
from app.db.session import get_session
from app.github_integration.client import GitHubError


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class _StubGitHubClient:
    def __init__(self, token):
        self.token = token
        self.pushed_paths = []

    def push_commit(self, local_path, branch="main"):
        self.pushed_paths.append(local_path)
        return "pushed-ok"


def _make_project(session, tmp_path, *, is_self=False, name="P1"):
    # Отдельный подкаталог, не сам tmp_path — conftest.db создаёт
    # test.sqlite3 в том же tmp_path, и если репо — это сам tmp_path,
    # `git status --porcelain` видит чужой файл БД как незакоммиченный,
    # что ложно триггерит коммит и ломает тест, не сам код.
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "f.txt").write_text("v1\n")
    _git("init", "-q", "-b", "main", cwd=repo_path)
    _git("config", "user.email", "t@example.com", cwd=repo_path)
    _git("config", "user.name", "T", cwd=repo_path)
    _git("add", "f.txt", cwd=repo_path)
    _git("commit", "-q", "-m", "initial", cwd=repo_path)
    project = Project(name=name, repo_full_name="owner/repo", local_path=str(repo_path), is_self=is_self)
    session.add(project)
    session.flush()
    return project.id, repo_path


def test_no_local_path_reports_message(db):
    with get_session() as session:
        project = Project(name="P1", repo_full_name="owner/repo", local_path=None)
        session.add(project)
        session.flush()
        project_id = project.id

    text = _manual_push_blocking(project_id, "token")

    assert "local_path" in text


def test_no_github_token_reports_message(tmp_path, db):
    with get_session() as session:
        project_id, _ = _make_project(session, tmp_path)

    text = _manual_push_blocking(project_id, None)

    assert "GITHUB_TOKEN" in text


def test_pushes_without_committing_when_nothing_uncommitted(tmp_path, db, monkeypatch):
    with get_session() as session:
        project_id, repo_path = _make_project(session, tmp_path)

    stub = _StubGitHubClient("token")
    monkeypatch.setattr(projects_module, "GitHubClient", lambda token: stub)

    text = _manual_push_blocking(project_id, "token")

    assert "Запушено" in text
    assert stub.pushed_paths == [repo_path]
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo_path, capture_output=True, text=True, check=True
    )
    assert log.stdout.count("\n") == 1  # только исходный коммит, ничего не добавилось


def test_commits_uncommitted_changes_before_pushing(tmp_path, db, monkeypatch):
    with get_session() as session:
        project_id, repo_path = _make_project(session, tmp_path)
    (repo_path / "f.txt").write_text("v2 — незакоммиченное изменение\n")

    stub = _StubGitHubClient("token")
    monkeypatch.setattr(projects_module, "GitHubClient", lambda token: stub)

    text = _manual_push_blocking(project_id, "token")

    assert "Запушено" in text
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo_path, capture_output=True, text=True, check=True
    )
    assert "Ручной пуш" in log.stdout


def test_works_for_self_check_project_too(tmp_path, db, monkeypatch):
    """Ключевая проверка: manual push не блокируется is_self — это ручной
    путь, а не автопуш ИИ."""
    with get_session() as session:
        project_id, repo_path = _make_project(session, tmp_path, is_self=True)

    stub = _StubGitHubClient("token")
    monkeypatch.setattr(projects_module, "GitHubClient", lambda token: stub)

    text = _manual_push_blocking(project_id, "token")

    assert "Запушено" in text
    assert stub.pushed_paths == [repo_path]


def test_push_failure_reported_without_crashing(tmp_path, db, monkeypatch):
    with get_session() as session:
        project_id, _ = _make_project(session, tmp_path)

    class _FailingClient:
        def __init__(self, token):
            pass

        def push_commit(self, local_path, branch="main"):
            raise GitHubError("network down")

    monkeypatch.setattr(projects_module, "GitHubClient", _FailingClient)

    text = _manual_push_blocking(project_id, "token")

    assert "не удался" in text.lower() or "❌" in text
