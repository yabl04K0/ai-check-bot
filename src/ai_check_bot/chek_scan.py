"""CHEK_PROTOCOL.md Step 2 (run the project's test command) and Step 4 (grep sweep for
known footguns) — the protocol steps that need only subprocess/filesystem access
against a target checkout, no AI agent loop. See chek_registry.py for Step 1/13 and
LAST_PROMPT.md for why Steps 4b-12 (the actual fleet) are a separate, larger effort.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDE_DIRS = frozenset({".git", "venv", ".venv", "__pycache__", "node_modules", "build", "dist"})

# Language generics from CHEK_PROTOCOL.md Step 4 item 2 — a project's own footguns (from
# its CLAUDE.md/PROJECT_MEMORY.md "known problems") are additional patterns the caller
# passes in, not hardcoded here.
DEFAULT_PYTHON_PATTERNS = [r"except\s*:\s*$", r"\bTODO\b", r"\bFIXME\b", r"\bHACK\b"]


# ---------------------------------------------------------------------------
# Step 2 — tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestResult:
    ran: bool
    command: str = ""
    passed: int | None = None
    failed: int | None = None
    output_tail: str = ""


def detect_test_command(project_path: Path) -> list[str] | None:
    """CHEK_PROTOCOL.md Step 2: derive the command from the project, do not hardcode
    one. Order matters where a project could match more than one (pytest before a
    generic tests/ dir check, since pyproject.toml commonly configures pytest)."""
    if (project_path / "pytest.ini").exists() or (project_path / "pyproject.toml").exists():
        return ["pytest", "-q"]
    if (project_path / "tests").is_dir():
        return ["pytest", "-q"]
    if (project_path / "package.json").exists():
        return ["npm", "test"]
    if (project_path / "Cargo.toml").exists():
        return ["cargo", "test"]
    if (project_path / "go.mod").exists():
        return ["go", "test", "./..."]
    return None


def _parse_pytest_summary(output: str) -> tuple[int | None, int | None]:
    # pytest-specific phrasing ("N passed"/"N failed"). npm/cargo/go test summaries use
    # different wording and are NOT parsed here — run_tests still executes them and
    # returns ran=True with passed=failed=None, output_tail carries the real summary for
    # a human/AI to read, rather than this function guessing at the wrong format.
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    if passed_match is None and failed_match is None:
        return None, None
    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    return passed, failed


def _tail(text: str, n: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def run_tests(project_path: Path, *, timeout: int = 300) -> TestResult:
    command = detect_test_command(project_path)
    if command is None:
        return TestResult(ran=False)
    try:
        proc = subprocess.run(
            command, cwd=project_path, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        return TestResult(ran=False, command=" ".join(command), output_tail=f"command not found: {exc}")
    except subprocess.TimeoutExpired:
        return TestResult(ran=False, command=" ".join(command), output_tail=f"timed out after {timeout}s")
    output = proc.stdout + proc.stderr
    passed, failed = _parse_pytest_summary(output)
    return TestResult(ran=True, command=" ".join(command), passed=passed, failed=failed, output_tail=_tail(output))


# ---------------------------------------------------------------------------
# Step 4 — grep sweeps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepHit:
    pattern: str
    path: str
    line_no: int
    line: str


def grep_sweep(
    project_path: Path,
    patterns: list[str],
    *,
    extensions: tuple[str, ...] = (".py",),
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> list[SweepHit]:
    """A fast project-wide sweep for CLASSES of bug, per CHEK_PROTOCOL.md Step 4. Pure
    Python (no external `grep`/`ripgrep` dependency) so it works the same regardless of
    what's installed on the host running the bot."""
    compiled = [(p, re.compile(p)) for p in patterns]
    hits: list[SweepHit] = []
    for path in sorted(project_path.rglob("*")):
        if not path.is_file() or path.suffix not in extensions:
            continue
        rel_path = path.relative_to(project_path)
        if exclude_dirs & set(rel_path.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_str, pattern in compiled:
                if pattern.search(line):
                    hits.append(
                        SweepHit(pattern=pattern_str, path=str(rel_path), line_no=line_no, line=line.strip())
                    )
    return hits
