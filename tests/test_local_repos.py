"""discover_local_repos/detect_repo_full_name — сканирование
LOCAL_REPOS_ROOT для удобного выбора репозитория кнопкой при добавлении
проекта (см. app/bot/handlers/projects.py)."""

from __future__ import annotations

import subprocess

from app.tasks.local_repos import detect_repo_full_name, discover_local_repos


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path):
    path.mkdir(parents=True)
    _git("init", "-q", cwd=path)


def test_discover_finds_repo_at_depth_one(tmp_path):
    _init_repo(tmp_path / "repo-a")
    (tmp_path / "not-a-repo").mkdir()

    found = discover_local_repos(tmp_path)

    assert found == [tmp_path / "repo-a"]


def test_discover_finds_repo_at_depth_two(tmp_path):
    _init_repo(tmp_path / "org" / "repo-b")

    found = discover_local_repos(tmp_path, max_depth=2)

    assert found == [tmp_path / "org" / "repo-b"]


def test_discover_does_not_descend_past_max_depth(tmp_path):
    _init_repo(tmp_path / "a" / "b" / "repo-deep")

    found = discover_local_repos(tmp_path, max_depth=1)

    assert found == []


def test_discover_does_not_look_inside_a_found_repo(tmp_path):
    """Репо внутри репо (например чекаут внутри чекаута) не должно
    находиться дважды — как только нашли .git, глубже не спускаемся."""
    repo = tmp_path / "repo-c"
    _init_repo(repo)
    _init_repo(repo / "nested")

    found = discover_local_repos(tmp_path, max_depth=2)

    assert found == [repo]


def test_discover_skips_hidden_directories(tmp_path):
    _init_repo(tmp_path / ".hidden")

    found = discover_local_repos(tmp_path)

    assert found == []


def test_discover_returns_empty_for_missing_root(tmp_path):
    assert discover_local_repos(tmp_path / "does-not-exist") == []


def test_detect_repo_full_name_from_https_remote(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git("remote", "add", "origin", "https://github.com/owner/repo-name.git", cwd=repo)

    assert detect_repo_full_name(repo) == "owner/repo-name"


def test_detect_repo_full_name_from_ssh_remote(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git("remote", "add", "origin", "git@github.com:owner/repo-name.git", cwd=repo)

    assert detect_repo_full_name(repo) == "owner/repo-name"


def test_detect_repo_full_name_none_without_remote(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    assert detect_repo_full_name(repo) is None


def test_detect_repo_full_name_none_for_non_github_remote(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git("remote", "add", "origin", "https://gitlab.com/owner/repo-name.git", cwd=repo)

    assert detect_repo_full_name(repo) is None
