"""CHEK_PROTOCOL.md Step 5: the fleet planner. Runs ONE read-only agentic call with the
protocol's own planner prompt (extracted from CHEK_PROTOCOL.md at runtime — see
chek_protocol_text.py) and parses its DOMAIN/PROMPT/SUMMARY output into a structured
fleet spec that Step 6 (not built yet) will run checkers against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ai_check_bot import agent_loop
from ai_check_bot.chek_protocol_text import extract_fenced_blocks, find_section, load_sections

PLANNER_ALLOWED_TOOLS = agent_loop.READ_ONLY_TOOLS
PLANNER_MAX_TURNS = 30


class PlannerOutputError(Exception):
    pass


class AgenticTaskNotSupportedError(Exception):
    pass


@dataclass(frozen=True)
class DomainSpec:
    name: str
    files: list[str]
    prompt: str


@dataclass(frozen=True)
class FleetSpec:
    domains: list[DomainSpec]
    covered: int | None
    total: int | None
    not_covered: list[str]

    def is_complete(self) -> bool:
        """Step 5's own completeness invariant, checked mechanically rather than trusted
        from the planner's self-reported SUMMARY line (Step 7's coverage check exists
        for exactly this reason — a planner can miscount)."""
        return self.covered is not None and self.total is not None and self.covered >= self.total


_DOMAIN_LINE_RE = re.compile(r"^DOMAIN\s+(?P<name>.+?)\s*\[.*?\]:\s*(?P<filelist>.*)$", re.MULTILINE)
_PROMPT_BLOCK_RE = re.compile(r"^PROMPT:\s*\n(?P<prompt>.*)", re.MULTILINE | re.DOTALL)
_SUMMARY_RE = re.compile(
    r"SUMMARY:.*?Files covered:\s*(?P<covered>\d+)\s*of\s*(?P<total>\d+)\.\s*Not covered:\s*(?P<not_covered>.*)",
    re.IGNORECASE,
)


def get_planner_prompt(protocol_text: str) -> str:
    sections = load_sections(protocol_text)
    body = find_section(sections, "STEP 5")
    blocks = extract_fenced_blocks(body)
    if not blocks:
        raise PlannerOutputError("CHEK_PROTOCOL.md Step 5 has no fenced prompt block")
    return blocks[0]


def parse_planner_output(text: str) -> FleetSpec:
    """CHEK_PROTOCOL.md Step 5's documented output shape:
        DOMAIN <name> [<N files>, <M lines>]: file1, file2, ...
        PROMPT:
        <the full sonnet checker prompt>
        ---
        (repeat per domain; then the contract domain)
        SUMMARY: N domain + 1 contract. Files covered: X of Y. Not covered: <list or "none">.
    A best-effort parse of free-form model output, not a strict format like
    chek_registry.py's self-authored files — raises PlannerOutputError rather than
    silently returning an incomplete/wrong spec when nothing parses."""
    domains: list[DomainSpec] = []
    for chunk in re.split(r"\n-{3,}\n", text):
        domain_match = _DOMAIN_LINE_RE.search(chunk)
        prompt_match = _PROMPT_BLOCK_RE.search(chunk)
        if domain_match is None or prompt_match is None:
            continue
        files = [f.strip() for f in domain_match.group("filelist").split(",") if f.strip()]
        domains.append(
            DomainSpec(name=domain_match.group("name").strip(), files=files, prompt=prompt_match.group("prompt").strip())
        )

    if not domains:
        raise PlannerOutputError("no DOMAIN/PROMPT blocks found in planner output")

    summary_match = _SUMMARY_RE.search(text)
    covered = total = None
    not_covered: list[str] = []
    if summary_match:
        covered = int(summary_match.group("covered"))
        total = int(summary_match.group("total"))
        raw_not_covered = summary_match.group("not_covered").strip().rstrip(".")
        if raw_not_covered and raw_not_covered.lower() != "none":
            not_covered = [f.strip() for f in raw_not_covered.split(",") if f.strip()]

    return FleetSpec(domains=domains, covered=covered, total=total, not_covered=not_covered)


async def run_fleet_planner(provider, root: Path, protocol_path: Path, *, extra_context: str = "") -> FleetSpec:
    if not hasattr(provider, "run_agentic_task"):
        raise AgenticTaskNotSupportedError(
            f"{type(provider).__name__} does not support run_agentic_task (no tool-use agent loop)"
        )
    system_prompt = get_planner_prompt(protocol_path.read_text(encoding="utf-8"))
    user_prompt = "Design the checker fleet for this project." + (f"\n\n{extra_context}" if extra_context else "")
    result = await provider.run_agentic_task(
        root, system_prompt, user_prompt, allowed_tools=PLANNER_ALLOWED_TOOLS, max_turns=PLANNER_MAX_TURNS
    )
    return parse_planner_output(result.final_text)
