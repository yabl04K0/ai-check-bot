"""File tools an agent-loop model can call (Read/Glob/Grep/Edit equivalents), sandboxed
to one target project root. Every function takes `root` explicitly and refuses to touch
anything outside it — a tool call is driven by model output, which can be wrong or, in
principle, adversarially crafted (a project file containing something that looks like an
instruction), so path containment is a security boundary, not a convenience check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 200_000  # a single huge file must not blow the model's context budget


class PathEscapesRootError(Exception):
    pass


class EditAmbiguousError(Exception):
    pass


class EditNotFoundError(Exception):
    pass


def _resolve_within_root(root: Path, rel_path: str) -> Path:
    root = root.resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapesRootError(f"'{rel_path}' resolves outside the project root")
    return candidate


def read_file(root: Path, rel_path: str) -> str:
    path = _resolve_within_root(root, rel_path)
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    data = path.read_bytes()
    truncated = len(data) > MAX_READ_BYTES
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n...(truncated, file is {len(data)} bytes, showing first {MAX_READ_BYTES})"
    return text


def list_files(root: Path, pattern: str = "**/*") -> list[str]:
    root = root.resolve()
    exclude_dirs = {".git", "venv", ".venv", "__pycache__", "node_modules", "build", "dist"}
    results = []
    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if exclude_dirs & set(rel.parts[:-1]):
            continue
        results.append(str(rel))
    return results


@dataclass(frozen=True)
class GrepHit:
    path: str
    line_no: int
    line: str


def grep(root: Path, pattern: str, *, glob_pattern: str = "**/*") -> list[GrepHit]:
    compiled = re.compile(pattern)
    hits: list[GrepHit] = []
    for rel_path in list_files(root, glob_pattern):
        path = root / rel_path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                hits.append(GrepHit(path=rel_path, line_no=line_no, line=line.strip()))
    return hits


def edit_file(root: Path, rel_path: str, old_string: str, new_string: str) -> None:
    """Same contract as Claude Code's own Edit tool: old_string must match EXACTLY ONE
    place in the file, or this refuses rather than guessing which occurrence was meant
    (EditAmbiguousError) or silently no-op'ing (EditNotFoundError)."""
    path = _resolve_within_root(root, rel_path)
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    text = path.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise EditNotFoundError(f"old_string not found in {rel_path}")
    if count > 1:
        raise EditAmbiguousError(f"old_string appears {count} times in {rel_path}, must be unique")
    path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")


def write_file(root: Path, rel_path: str, content: str) -> None:
    """Creates a new file (or fully overwrites one) — for the fixer/test-writer roles
    adding a new file, unlike edit_file's find-and-replace on an existing one."""
    path = _resolve_within_root(root, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
