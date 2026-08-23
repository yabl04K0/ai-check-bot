from pathlib import Path

from ai_check_bot import chek_scan as scan


# ---------------------------------------------------------------------------
# Step 2 — tests
# ---------------------------------------------------------------------------


def test_detect_test_command_pytest_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    assert scan.detect_test_command(tmp_path) == ["pytest", "-q"]


def test_detect_test_command_tests_dir(tmp_path):
    (tmp_path / "tests").mkdir()
    assert scan.detect_test_command(tmp_path) == ["pytest", "-q"]


def test_detect_test_command_package_json(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert scan.detect_test_command(tmp_path) == ["npm", "test"]


def test_detect_test_command_none_found(tmp_path):
    assert scan.detect_test_command(tmp_path) is None


def test_run_tests_no_command_found(tmp_path):
    result = scan.run_tests(tmp_path)
    assert result.ran is False
    assert result.passed is None


def test_run_tests_real_pytest_all_passing(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_sample.py").write_text(
        "def test_one():\n    assert 1 == 1\n\ndef test_two():\n    assert True\n"
    )
    result = scan.run_tests(tmp_path)
    assert result.ran is True
    assert result.passed == 2
    assert result.failed == 0


def test_run_tests_real_pytest_with_failure(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n\ndef test_broken():\n    assert False\n"
    )
    result = scan.run_tests(tmp_path)
    assert result.ran is True
    assert result.passed == 1
    assert result.failed == 1


def test_run_tests_command_not_found(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    result = scan.run_tests(tmp_path)
    # cargo may or may not be installed on the host — either a real run or a clean
    # "command not found", never an unhandled exception either way.
    assert result.command == "cargo test"


# ---------------------------------------------------------------------------
# Step 4 — grep sweeps
# ---------------------------------------------------------------------------


def test_grep_sweep_finds_bare_except(tmp_path):
    (tmp_path / "a.py").write_text("try:\n    x()\nexcept:\n    pass\n")
    hits = scan.grep_sweep(tmp_path, [r"except\s*:\s*$"])
    assert len(hits) == 1
    assert hits[0].path == "a.py"
    assert hits[0].line_no == 3


def test_grep_sweep_no_hits_on_clean_code(tmp_path):
    (tmp_path / "a.py").write_text("try:\n    x()\nexcept ValueError:\n    pass\n")
    hits = scan.grep_sweep(tmp_path, [r"except\s*:\s*$"])
    assert hits == []


def test_grep_sweep_excludes_venv_dir(tmp_path):
    venv_dir = tmp_path / "venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "b.py").write_text("except:\n    pass\n")
    (tmp_path / "a.py").write_text("except:\n    pass\n")
    hits = scan.grep_sweep(tmp_path, [r"except\s*:\s*$"])
    assert [h.path for h in hits] == ["a.py"]


def test_grep_sweep_multiple_patterns(tmp_path):
    (tmp_path / "a.py").write_text("# TODO: fix this\nx = 1\n")
    hits = scan.grep_sweep(tmp_path, scan.DEFAULT_PYTHON_PATTERNS)
    assert any(h.pattern == r"\bTODO\b" for h in hits)


def test_grep_sweep_only_scans_given_extensions(tmp_path):
    (tmp_path / "a.txt").write_text("except:\n")
    hits = scan.grep_sweep(tmp_path, [r"except\s*:\s*$"], extensions=(".py",))
    assert hits == []
