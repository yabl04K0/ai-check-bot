import pytest

from ai_check_bot import agent_tools as tools


# ---------------------------------------------------------------------------
# path containment — the security boundary
# ---------------------------------------------------------------------------


def test_read_file_rejects_parent_traversal(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "secret.txt").write_text("s3cr3t")
    with pytest.raises(tools.PathEscapesRootError):
        tools.read_file(tmp_path / "project", "../secret.txt")


def test_read_file_rejects_absolute_path_escape(tmp_path):
    (tmp_path / "project").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")
    with pytest.raises(tools.PathEscapesRootError):
        tools.read_file(tmp_path / "project", f"../{outside.name}")


def test_edit_file_rejects_traversal(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "secret.txt").write_text("s3cr3t")
    with pytest.raises(tools.PathEscapesRootError):
        tools.edit_file(project, "../secret.txt", "s3cr3t", "pwned")


def test_write_file_rejects_traversal(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(tools.PathEscapesRootError):
        tools.write_file(project, "../escaped.txt", "x")


def test_read_file_within_root_is_fine(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    assert tools.read_file(tmp_path, "a.py") == "print(1)\n"


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


def test_read_file_missing_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        tools.read_file(tmp_path, "nope.py")


def test_read_file_truncates_huge_file(tmp_path):
    (tmp_path / "big.txt").write_text("x" * (tools.MAX_READ_BYTES + 1000))
    text = tools.read_file(tmp_path, "big.txt")
    assert "truncated" in text
    assert len(text) < tools.MAX_READ_BYTES + 200


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_list_files_excludes_venv(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "b.py").write_text("x")
    assert tools.list_files(tmp_path, "**/*.py") == ["a.py"]


def test_list_files_only_matches_pattern(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    assert tools.list_files(tmp_path, "**/*.py") == ["a.py"]


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


def test_grep_finds_matches(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    hits = tools.grep(tmp_path, r"def foo")
    assert len(hits) == 1
    assert hits[0].path == "a.py"
    assert hits[0].line_no == 1


def test_grep_no_matches(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert tools.grep(tmp_path, r"def foo") == []


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


def test_edit_file_replaces_unique_match(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    tools.edit_file(tmp_path, "a.py", "x = 1", "x = 100")
    assert (tmp_path / "a.py").read_text() == "x = 100\ny = 2\n"


def test_edit_file_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        tools.edit_file(tmp_path, "nope.py", "a", "b")


def test_edit_file_old_string_not_found_raises(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(tools.EditNotFoundError):
        tools.edit_file(tmp_path, "a.py", "y = 2", "y = 3")


def test_edit_file_ambiguous_match_raises(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(tools.EditAmbiguousError):
        tools.edit_file(tmp_path, "a.py", "x = 1", "x = 2")
    # and the file must be UNCHANGED — a rejected edit is not a partial edit
    assert (tmp_path / "a.py").read_text() == "x = 1\nx = 1\n"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


def test_write_file_creates_new_file(tmp_path):
    tools.write_file(tmp_path, "new.py", "print(1)\n")
    assert (tmp_path / "new.py").read_text() == "print(1)\n"


def test_write_file_creates_parent_dirs(tmp_path):
    tools.write_file(tmp_path, "sub/dir/new.py", "x\n")
    assert (tmp_path / "sub" / "dir" / "new.py").read_text() == "x\n"


def test_write_file_overwrites_existing(tmp_path):
    (tmp_path / "a.py").write_text("old\n")
    tools.write_file(tmp_path, "a.py", "new\n")
    assert (tmp_path / "a.py").read_text() == "new\n"
