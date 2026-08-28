from __future__ import annotations

import subprocess

from app.bot.handlers.projects import _discard_changes_blocking
from app.db.models import Project
from app.db.session import get_session


def _git(*args: str, cwd) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _make_project(session, tmp_path, *, name="P1"):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "f.txt").write_text("v1\n")
    _git("init", "-q", "-b", "main", cwd=repo_path)
    _git("config", "user.email", "t@example.com", cwd=repo_path)
    _git("config", "user.name", "T", cwd=repo_path)
    _git("add", "f.txt", cwd=repo_path)
    _git("commit", "-q", "-m", "initial", cwd=repo_path)
    project = Project(name=name, repo_full_name="owner/repo", local_path=str(repo_path))
    session.add(project)
    session.flush()
    return project.id, repo_path


def test_no_local_path_reports_message(db):
    with get_session() as session:
        project = Project(name="P1", repo_full_name="owner/repo", local_path=None)
        session.add(project)
        session.flush()
        project_id = project.id

    text = _discard_changes_blocking(project_id)

    assert "local_path" in text


def test_reverts_tracked_file_change(tmp_path, db):
    with get_session() as session:
        project_id, repo_path = _make_project(session, tmp_path)
    (repo_path / "f.txt").write_text("v2 — незакоммиченное изменение\n")

    text = _discard_changes_blocking(project_id)

    assert "✅" in text
    assert (repo_path / "f.txt").read_text() == "v1\n"


def test_leaves_untracked_files_alone(tmp_path, db):
    with get_session() as session:
        project_id, repo_path = _make_project(session, tmp_path)
    (repo_path / "new.txt").write_text("keep me\n")

    text = _discard_changes_blocking(project_id)

    assert "✅" in text
    assert (repo_path / "new.txt").exists()


def test_project_not_found_reports_message(db):
    text = _discard_changes_blocking(999999)
    assert "не найден" in text
