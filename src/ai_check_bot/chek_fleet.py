"""CHEK_PROTOCOL.md Steps 5-6: the fleet planner and the domain checkers it specifies.
Prompts are extracted from CHEK_PROTOCOL.md at runtime (see chek_protocol_text.py), never
copied into a Python string — the protocol file is the single source, on purpose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ai_check_bot import agent_loop, jobs
from ai_check_bot.chek_protocol_text import extract_fenced_blocks, find_section, load_sections

PLANNER_ALLOWED_TOOLS = agent_loop.READ_ONLY_TOOLS
PLANNER_MAX_TURNS = 30
CHECKER_ALLOWED_TOOLS = agent_loop.READ_ONLY_TOOLS
CHECKER_MAX_TURNS = 30


class PlannerOutputError(Exception):
    pass


class AgenticTaskNotSupportedError(Exception):
    pass


def _require_agentic(provider) -> None:
    if not hasattr(provider, "run_agentic_task"):
        raise AgenticTaskNotSupportedError(
            f"{type(provider).__name__} does not support run_agentic_task (no tool-use agent loop)"
        )


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
    _require_agentic(provider)
    system_prompt = get_planner_prompt(protocol_path.read_text(encoding="utf-8"))
    user_prompt = "Design the checker fleet for this project." + (f"\n\n{extra_context}" if extra_context else "")
    result = await provider.run_agentic_task(
        root, system_prompt, user_prompt, allowed_tools=PLANNER_ALLOWED_TOOLS, max_turns=PLANNER_MAX_TURNS
    )
    return parse_planner_output(result.final_text)


# ============================================================================
# Step 6 — fleet checkers
# ============================================================================


@dataclass(frozen=True)
class Finding:
    severity: str
    file: str
    line: int
    description: str


@dataclass(frozen=True)
class CheckerReport:
    domain: str
    findings: list[Finding] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    hit_turn_limit: bool = False
    raw_text: str = ""
    error: str | None = None  # set when the output could not be parsed — the domain
    # still appears in run_checkers()'s result (never silently dropped), with empty
    # files_read so Step 7's coverage check correctly flags its files as unread.


class CheckerOutputError(Exception):
    pass


_FINDING_RE = re.compile(
    r"^(?P<severity>CRITICAL|HIGH|MEDIUM|КРИТИЧНО|ВЫСОКИЙ|СРЕДНИЙ)\s+"
    r"(?P<file>\S+?):(?P<line>\d+)\s*[—-]\s*(?P<description>.+)$",
    re.MULTILINE,
)
_READ_LINE_RE = re.compile(r"^(?:Read|Прочитано)\s*:\s*(?P<files>.+)$", re.MULTILINE | re.IGNORECASE)


def get_checker_common_rules(protocol_text: str) -> str:
    """Step 6's own text — 'Common rules appended to EVERY checker prompt' — read
    straight out of CHEK_PROTOCOL.md rather than duplicated, same reasoning as the
    planner prompt above."""
    sections = load_sections(protocol_text)
    return find_section(sections, "STEP 6").strip()


def build_checker_prompt(domain_prompt: str, common_rules: str, suppression_block: str = "") -> str:
    """The planner's domain-specific prompt (a-c per its own instructions) plus Step 6's
    common tail plus, when present, Step 1's 'ALREADY SETTLED' suppression block."""
    parts = [domain_prompt.strip(), common_rules.strip()]
    if suppression_block.strip():
        parts.append(suppression_block.strip())
    return "\n\n".join(parts)


def parse_checker_output(text: str) -> tuple[list[Finding], list[str]]:
    """CHEK_PROTOCOL.md Step 6 rules 7/10: one finding per line as `SEVERITY file:line —
    description`, and a final `Read: file1, file2, ...` line. Best-effort against
    free-form model output — a checker that reports nothing legitimately produces zero
    findings (that is CLEAN, not an error); this only raises when the files-read line
    itself is missing, since Step 7's coverage check depends on it existing."""
    findings = [
        Finding(
            severity=m.group("severity"),
            file=m.group("file"),
            line=int(m.group("line")),
            description=m.group("description").strip(),
        )
        for m in _FINDING_RE.finditer(text)
    ]
    read_match = _READ_LINE_RE.search(text)
    if read_match is None:
        raise CheckerOutputError("no 'Read: file1, file2, ...' line found in checker output")
    files_read = [f.strip() for f in read_match.group("files").split(",") if f.strip()]
    return findings, files_read


async def run_checkers(
    provider,
    root: Path,
    fleet_spec: FleetSpec,
    protocol_path: Path,
    *,
    suppression_block: str = "",
    on_progress=None,
) -> dict[str, CheckerReport]:
    """Step 6: launch every domain checker CONCURRENTLY (CHEK_PROTOCOL.md: 'MANDATORY:
    launch ALL agents from the fleet spec IN ONE MESSAGE') via jobs.run_workers_parallel
    — reusing the live-status engine rather than building a second one. A checker whose
    output cannot be parsed (missing the Read: line) is recorded as a failed worker, not
    silently dropped from the fleet — Step 7's coverage check needs to know it happened."""
    _require_agentic(provider)
    common_rules = get_checker_common_rules(protocol_path.read_text(encoding="utf-8"))
    domains_by_name = {d.name: d for d in fleet_spec.domains}
    reports: dict[str, CheckerReport] = {}

    async def run_one(name: str) -> str:
        domain = domains_by_name[name]
        prompt = build_checker_prompt(domain.prompt, common_rules, suppression_block)
        result = await provider.run_agentic_task(
            root, prompt, "Audit your assigned domain.", allowed_tools=CHECKER_ALLOWED_TOOLS, max_turns=CHECKER_MAX_TURNS
        )
        try:
            findings, files_read = parse_checker_output(result.final_text)
        except CheckerOutputError as exc:
            # Caught HERE, not left to jobs.run_workers_parallel's generic handler: that
            # only marks the Job's own worker state, it never touches `reports` — a
            # domain would silently vanish from the dict this function returns, which is
            # exactly the "silently dropped" outcome the docstring above promises does
            # NOT happen. Recording it here keeps every domain in `reports`.
            reports[name] = CheckerReport(domain=name, hit_turn_limit=result.hit_turn_limit, raw_text=result.final_text, error=str(exc))
            raise  # still surfaces as a failed job worker for the live-status display
        reports[name] = CheckerReport(
            domain=name,
            findings=findings,
            files_read=files_read,
            hit_turn_limit=result.hit_turn_limit,
            raw_text=result.final_text,
        )
        return f"{len(findings)} findings"

    job = jobs.create_job("Fleet checkers", list(domains_by_name))
    await jobs.run_workers_parallel(
        job, list(domains_by_name), run_one, on_progress=on_progress or _default_on_progress
    )
    return reports


async def _default_on_progress(job: jobs.Job) -> None:
    return None
