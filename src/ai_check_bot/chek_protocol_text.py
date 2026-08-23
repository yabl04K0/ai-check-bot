"""Extracts step sections and their fenced prompt blocks out of CHEK_PROTOCOL.md at
runtime. CHEK_PROTOCOL.md's own header says it is "the ONLY copy of the protocol body";
copying a prompt into a Python string constant would make a second copy that silently
drifts the next time the AI-kit sync pulls an update from the structure repo (see
tools/ai_kit.json). Reading the file and parsing out what's needed keeps that invariant.
"""
from __future__ import annotations

import re

_SECTION_HEADER_RE = re.compile(r"^# =+\n# (.+)\n# =+\n", re.MULTILINE)
_FENCE_RE = re.compile(r"```\n(.*?)```", re.DOTALL)


class SectionNotFoundError(Exception):
    pass


def load_sections(protocol_text: str) -> dict[str, str]:
    """Splits the file into {full section title: section body text}, using the
    '# ===...\\n# TITLE\\n# ===...' header convention every CHEK_PROTOCOL.md step uses."""
    matches = list(_SECTION_HEADER_RE.finditer(protocol_text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(protocol_text)
        sections[title] = protocol_text[body_start:body_end]
    return sections


def find_section(sections: dict[str, str], title_prefix: str) -> str:
    """title_prefix matches e.g. 'STEP 5' against the full title 'STEP 5 — fleet planner
    (...)' — callers do not need to quote the whole heading. Word-boundary match, not a
    bare .startswith(): 'STEP 10'/'STEP 11'/'STEP 12'/'STEP 13' all start with the plain
    string 'STEP 1', which would make find_section(sections, 'STEP 1') silently return
    the wrong section were it not for this check (it currently "works" only by the luck
    of dict insertion order putting the real Step 1 first — that is exactly the kind of
    latent bug this word-boundary check removes rather than relying on)."""
    for title, body in sections.items():
        if title == title_prefix or title.startswith(title_prefix + " "):
            return body
    raise SectionNotFoundError(f"no section starting with {title_prefix!r} found")


def extract_fenced_blocks(section_text: str) -> list[str]:
    """Every ``` ... ``` fenced block in a section, in document order. A step with one
    embedded prompt (Step 5, 8, 9, 12) has one block; Step 10 has two (critic A, critic
    B); Step 11 has three (intermediate verifier, follow-up fixer — Step 10's blocks are
    reused for its own final round, not repeated)."""
    return [m.group(1).rstrip("\n") for m in _FENCE_RE.finditer(section_text)]
