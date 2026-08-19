"""_apply_and_commit_blocking (commit_yes) — self-check должен коммитить,
но НИКОГДА не пушить сам, даже если autopush_enabled=True для проекта
(см. README, "Нефункциональные требования"). is_self раньше нигде не
выставлялся (см. app/bot/handlers/projects.py toggle_self_check), так
что эта защита реально не срабатывала ни разу — тест фиксирует, что
теперь срабатывает."""

from __future__ import annotations

import subprocess

from app.bot.handlers import check as check_module
from app.db.models import Job, Project, TaskType
from app.db.session import get_session

DIFF = (
    "--- a/hello.txt\n"
    "+++ b/hello.txt\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(tmp_path) -> None:
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "T", cwd=tmp_path)
    (tmp_path / "hello.txt").write_text("old\n")
    _git("add", "hello.txt", cwd=tmp_path)
    _git("commit", "-q", "-m", "init", cwd=tmp_path)


class _StubGitHubClient:
    push_calls: list[str] = []

    def __init__(self, token: str) -> None:
        self.token = token

    def push_commit(self, path, branch: str = "main") -> str:
        _StubGitHubClient.push_calls.append(str(path))
        return "stub-push-ok"


def _make_job(session, project, patch_text: str) -> int:
    job = Job(task_type=TaskType.FIX, comment="тест", patch_text=patch_text, progress_total=1)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job.id


def test_self_check_commits_but_never_pushes(db, tmp_path, monkeypatch):
    _StubGitHubClient.push_calls.clear()
    monkeypatch.setattr(check_module, "GitHubClient", _StubGitHubClient)
    _init_repo(tmp_path)

    with get_session() as session:
        project = Project(
            name="Bot itself",
            repo_full_name="owner/ai-check-bot",
            local_path=str(tmp_path),
            is_self=True,
            autopush_enabled=True,  # намеренно вкл — is_self должен всё равно перебить
        )
        session.add(project)
        session.flush()
        job_id = _make_job(session, project, DIFF)
        session.commit()

    text = check_module._apply_and_commit_blocking(job_id, "fake-github-token")

    assert "Закоммичено" in text
    assert "self-check" in text
    assert _StubGitHubClient.push_calls == []  # push НЕ вызывался

    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert "тест" in log.stdout
    assert (tmp_path / "hello.txt").read_text() == "new\n"


def test_non_self_project_with_autopush_does_push(db, tmp_path, monkeypatch):
    _StubGitHubClient.push_calls.clear()
    monkeypatch.setattr(check_module, "GitHubClient", _StubGitHubClient)
    _init_repo(tmp_path)

    with get_session() as session:
        project = Project(
            name="Other project",
            repo_full_name="owner/other",
            local_path=str(tmp_path),
            is_self=False,
            autopush_enabled=True,
        )
        session.add(project)
        session.flush()
        job_id = _make_job(session, project, DIFF)
        session.commit()

    text = check_module._apply_and_commit_blocking(job_id, "fake-github-token")

    assert "Закоммичено" in text
    assert "Запушено" in text
    assert _StubGitHubClient.push_calls == [str(tmp_path)]


def test_autopush_disabled_does_not_push(db, tmp_path, monkeypatch):
    _StubGitHubClient.push_calls.clear()
    monkeypatch.setattr(check_module, "GitHubClient", _StubGitHubClient)
    _init_repo(tmp_path)

    with get_session() as session:
        project = Project(
            name="Other project",
            repo_full_name="owner/other",
            local_path=str(tmp_path),
            is_self=False,
            autopush_enabled=False,
        )
        session.add(project)
        session.flush()
        job_id = _make_job(session, project, DIFF)
        session.commit()

    text = check_module._apply_and_commit_blocking(job_id, "fake-github-token")

    assert "Закоммичено" in text
    assert _StubGitHubClient.push_calls == []
