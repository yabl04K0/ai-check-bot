"""Parses and rewrites the three CHEK registry files (chek_open.md, chek_never.md,
chek_later.md) per the entry format each file documents in its own header.

This is CHEK_PROTOCOL.md Step 1 (load every registry, check the "each id in exactly
one file" invariant) and part of Step 13 (append/remove entries on the human's
decision) implemented as real, tested code — ahead of the fleet orchestration
(Steps 4b-12) that will eventually call it. See LAST_PROMPT.md for why the fleet
itself is a separate, larger piece of work, not bundled in here.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_MARKERS = {
    "open": "# === open problems ===",
    "never": "# --- entries below ---",
    "later": "# === later ===",
}
_PLACEHOLDER = "(none)"


@dataclass
class OpenEntry:
    id: str
    severity: str
    status: str = "open"
    passes_run: int = 0
    passes_life: int = 1
    first_seen: str = ""
    attempts: list[str] = field(default_factory=list)
    subfindings: list[str] = field(default_factory=list)


@dataclass
class NeverEntry:
    id: str
    severity: str
    reason: str
    added: str = ""


@dataclass
class LaterEntry:
    id: str
    severity: str
    deferred_reason: str
    first_seen: str = ""
    deferred_sha: str | None = None
    remind_when: str | None = None


_KIND_TO_CLASS = {"open": OpenEntry, "never": NeverEntry, "later": LaterEntry}


class RegistryFormatError(Exception):
    pass


def _entries_block(text: str, kind: str) -> tuple[int, int, str]:
    """Return (start_line, end_line, raw_text) for the entries region: the lines right
    after the kind's marker, up to the next '#'-prefixed line or end of file."""
    marker = _MARKERS[kind]
    lines = text.splitlines()
    try:
        marker_idx = lines.index(marker)
    except ValueError:
        raise RegistryFormatError(f"marker '{marker}' not found — not a {kind} registry file") from None
    start = marker_idx + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("#"):
            end = i
            break
    return start, end, "\n".join(lines[start:end]).strip()


def parse(text: str, kind: str) -> list:
    """Empty/placeholder content ("(none)", "(none yet)", or any other non-list prose)
    parses to []  — only a block that actually starts a YAML list is real entries."""
    _, _, block = _entries_block(text, kind)
    if not block.startswith("-"):
        return []
    try:
        raw = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise RegistryFormatError(f"invalid YAML in {kind} registry entries block: {exc}") from exc
    cls = _KIND_TO_CLASS[kind]
    try:
        return [cls(**item) for item in raw]
    except TypeError as exc:
        raise RegistryFormatError(f"entry does not match the {kind} registry format: {exc}") from exc


def _render_block(entries: list) -> str:
    if not entries:
        return _PLACEHOLDER
    dicts = [
        {k: v for k, v in dataclasses.asdict(e).items() if v not in (None, [], "")}
        for e in entries
    ]
    return yaml.dump(dicts, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")


def _replace_block(path: Path, kind: str, entries: list) -> None:
    text = path.read_text(encoding="utf-8")
    start, end, _ = _entries_block(text, kind)
    lines = text.splitlines()
    new_lines = lines[:start] + ["", _render_block(entries)] + lines[end:]
    path.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")


def append_entry(path: Path, kind: str, entry) -> None:
    entries = parse(path.read_text(encoding="utf-8"), kind)
    if any(e.id == entry.id for e in entries):
        raise ValueError(f"id '{entry.id}' already present in {path.name}")
    entries.append(entry)
    _replace_block(path, kind, entries)


def remove_entry(path: Path, kind: str, entry_id: str) -> bool:
    """True if `entry_id` was found and removed, False if it was not present."""
    entries = parse(path.read_text(encoding="utf-8"), kind)
    remaining = [e for e in entries if e.id != entry_id]
    if len(remaining) == len(entries):
        return False
    _replace_block(path, kind, remaining)
    return True


def check_duplicate_ids(open_path: Path, never_path: Path, later_path: Path) -> dict[str, list[str]]:
    """CHEK_PROTOCOL.md Step 1 invariant #1: every id lives in at most ONE registry.
    Returns {id: [kinds it appears in]} for every id that violates this — empty if
    the registries are clean."""
    by_id: dict[str, list[str]] = {}
    for path, kind in ((open_path, "open"), (never_path, "never"), (later_path, "later")):
        for entry in parse(path.read_text(encoding="utf-8"), kind):
            by_id.setdefault(entry.id, []).append(kind)
    return {id_: kinds for id_, kinds in by_id.items() if len(kinds) > 1}
