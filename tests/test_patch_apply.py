from __future__ import annotations

import subprocess

from app.tasks.patch_apply import apply_patch, clean_patch_text, commit_all, current_commit_sha


def _git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _init_repo(tmp_path) -> None:
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "T", cwd=tmp_path)
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3\n")
    _git("add", "hello.txt", cwd=tmp_path)
    _git("commit", "-q", "-m", "init", cwd=tmp_path)


def test_clean_patch_text_strips_markdown_fence():
    wrapped = "```diff\n--- a/x\n+++ b/x\n```\n"
    assert clean_patch_text(wrapped) == "--- a/x\n+++ b/x\n"


def test_clean_patch_text_leaves_plain_diff_untouched():
    plain = "--- a/x\n+++ b/x\n"
    assert clean_patch_text(plain) == plain


def test_apply_patch_empty_text_fails():
    ok, detail = apply_patch(".", "   \n")
    assert ok is False
    assert "пуст" in detail


def test_apply_patch_valid_diff_writes_file(tmp_path):
    _init_repo(tmp_path)
    diff = (
        "--- a/hello.txt\n"
        "+++ b/hello.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2 changed\n"
        " line3\n"
    )
    ok, detail = apply_patch(tmp_path, diff)
    assert ok is True, detail
    assert (tmp_path / "hello.txt").read_text() == "line1\nline2 changed\nline3\n"


def test_apply_patch_invalid_diff_fails_cleanly(tmp_path):
    _init_repo(tmp_path)
    ok, detail = apply_patch(tmp_path, "not a real diff at all")
    assert ok is False
    assert detail


def test_commit_all_creates_commit(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("added\n")
    ok, detail = commit_all(tmp_path, "add new.txt")
    assert ok is True, detail

    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert log.stdout.strip() == "add new.txt"


def test_commit_all_nothing_to_commit_fails(tmp_path):
    _init_repo(tmp_path)
    ok, detail = commit_all(tmp_path, "empty commit attempt")
    assert ok is False
    assert detail


def test_current_commit_sha_returns_hash(tmp_path):
    _init_repo(tmp_path)
    sha = current_commit_sha(tmp_path)
    assert sha is not None
    assert len(sha) == 40


def test_current_commit_sha_none_when_not_a_repo(tmp_path):
    assert current_commit_sha(tmp_path) is None
