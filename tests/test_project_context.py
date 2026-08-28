from __future__ import annotations

import subprocess

from app.db.models import Project
from app.registry_store.store import Registry, RegistryFinding, write_registry
from app.tasks import project_context as ctxdata


def _project(path=None) -> Project:
    return Project(name="P", repo_full_name="owner/p", local_path=str(path) if path else None)


def test_local_path_none_when_not_set():
    assert ctxdata.local_path(_project()) is None


def test_local_path_none_when_dir_missing(tmp_path):
    missing = tmp_path / "nope"
    assert ctxdata.local_path(_project(missing)) is None


def test_local_path_returns_dir_when_exists(tmp_path):
    assert ctxdata.local_path(_project(tmp_path)) == tmp_path


def test_gather_registry_unavailable_without_local_path():
    assert ctxdata.gather_registry(_project()) == ctxdata.UNAVAILABLE


def test_gather_registry_lists_open_findings(tmp_path):
    write_registry(
        tmp_path,
        Registry(open=[RegistryFinding(file_symbol="a.py::f", description="сломано", severity="high")]),
    )
    text = ctxdata.gather_registry(_project(tmp_path))
    assert "Открыто: 1" in text
    assert "a.py::f" in text
    assert "сломано" in text


def test_gather_project_memory_unavailable_without_local_path():
    assert ctxdata.gather_project_memory(_project()) == ctxdata.UNAVAILABLE


def test_gather_project_memory_placeholder_when_no_file(tmp_path):
    assert ctxdata.gather_project_memory(_project(tmp_path)) == ctxdata.NO_PROJECT_MEMORY


def test_gather_project_memory_excludes_session_log(tmp_path):
    (tmp_path / "PROJECT_MEMORY.md").write_text(
        "# STRUCTURE\narchitecture notes here\n\n# SESSION LOG\n\n--- entry ---\nold\n", encoding="utf-8"
    )
    text = ctxdata.gather_project_memory(_project(tmp_path))
    assert "architecture notes here" in text
    assert "old" not in text


def test_gather_last_prompt_unavailable_without_local_path():
    assert ctxdata.gather_last_prompt(_project()) == ctxdata.UNAVAILABLE


def test_gather_last_prompt_placeholder_when_no_file(tmp_path):
    assert ctxdata.gather_last_prompt(_project(tmp_path)) == ctxdata.NO_LAST_PROMPT


def test_gather_last_prompt_reads_file(tmp_path):
    (tmp_path / "LAST_PROMPT.md").write_text("Продолжи с шага 3\n", encoding="utf-8")
    assert ctxdata.gather_last_prompt(_project(tmp_path)) == "Продолжи с шага 3"


def test_gather_logs_no_logs_dir(tmp_path):
    assert ctxdata.gather_logs(_project(tmp_path)) == "(папки logs/ нет)"


def test_gather_logs_reads_tail(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "app.log").write_text("line1\nline2\nline3\n")
    text = ctxdata.gather_logs(_project(tmp_path))
    assert "app.log" in text
    assert "line3" in text


def test_sweep_finds_markers(tmp_path):
    (tmp_path / "mod.py").write_text("x = 1  # TODO: fix this\n")
    text = ctxdata.sweep(_project(tmp_path))
    assert "TODO" in text
    assert "mod.py" in text


def test_sweep_no_markers(tmp_path):
    (tmp_path / "mod.py").write_text("x = 1\n")
    text = ctxdata.sweep(_project(tmp_path))
    assert "не найдено" in text


def test_sweep_path_filter_restricts_to_subpath(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "mod.py").write_text("# TODO: in a\n")
    (tmp_path / "b" / "mod.py").write_text("# TODO: in b\n")

    text = ctxdata.sweep(_project(tmp_path), path_filter="a")
    assert "in a" in text
    assert "in b" not in text


def test_sweep_path_filter_rejects_traversal_outside_project(tmp_path):
    (tmp_path / "mod.py").write_text("# TODO: safe\n")
    text = ctxdata.sweep(_project(tmp_path), path_filter="../../etc")
    assert "за пределы проекта" in text


def test_sweep_path_filter_missing_path(tmp_path):
    text = ctxdata.sweep(_project(tmp_path), path_filter="does/not/exist")
    assert "не найден" in text


def test_gather_tests_no_tests_present(tmp_path):
    text = ctxdata.gather_tests(_project(tmp_path))
    assert "не найдено" in text


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


def test_stash_check_clean_repo(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    ok, detail = ctxdata.stash_check(_project(tmp_path))
    assert ok is True
    assert "чист" in detail


def test_stash_check_detects_dirty_stash(tmp_path):
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "T", cwd=tmp_path)
    (tmp_path / "f.txt").write_text("hello\n")
    _git("add", "f.txt", cwd=tmp_path)
    _git("commit", "-q", "-m", "init", cwd=tmp_path)
    (tmp_path / "f.txt").write_text("changed\n")
    _git("stash", "-q", cwd=tmp_path)

    ok, detail = ctxdata.stash_check(_project(tmp_path))
    assert ok is False
    assert "незавершённая работа" in detail
