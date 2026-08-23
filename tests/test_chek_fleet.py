import asyncio
from pathlib import Path

import pytest

from ai_check_bot import agent_loop, chek_fleet, jobs

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_OUTPUT = """\
DOMAIN core [3 files, 450 lines]: src/a.py, src/b.py, src/c.py
PROMPT:
You are auditing the core subsystem.
Look for races and resource leaks.
---
DOMAIN contract [2 files, 120 lines]: src/d.py, src/e.py
PROMPT:
You are the contract checker.
Check seams between domains.
---
SUMMARY: 1 domain + 1 contract. Files covered: 5 of 5. Not covered: none.
"""

SAMPLE_OUTPUT_WITH_GAPS = """\
DOMAIN core [1 files, 10 lines]: src/a.py
PROMPT:
Audit core.
---
SUMMARY: 1 domain + 1 contract. Files covered: 1 of 3. Not covered: src/b.py, src/c.py.
"""


# ---------------------------------------------------------------------------
# get_planner_prompt — against the REAL CHEK_PROTOCOL.md
# ---------------------------------------------------------------------------


def test_get_planner_prompt_from_real_file():
    text = (REPO_ROOT / "CHEK_PROTOCOL.md").read_text(encoding="utf-8")
    prompt = chek_fleet.get_planner_prompt(text)
    assert "audit architect" in prompt
    assert "DOMAIN <name>" in prompt


# ---------------------------------------------------------------------------
# parse_planner_output
# ---------------------------------------------------------------------------


def test_parse_planner_output_two_domains():
    spec = chek_fleet.parse_planner_output(SAMPLE_OUTPUT)
    assert len(spec.domains) == 2
    assert spec.domains[0].name == "core"
    assert spec.domains[0].files == ["src/a.py", "src/b.py", "src/c.py"]
    assert "races and resource leaks" in spec.domains[0].prompt
    assert spec.domains[1].name == "contract"


def test_parse_planner_output_summary_covered_and_none_missing():
    spec = chek_fleet.parse_planner_output(SAMPLE_OUTPUT)
    assert spec.covered == 5
    assert spec.total == 5
    assert spec.not_covered == []
    assert spec.is_complete() is True


def test_parse_planner_output_not_covered_list():
    spec = chek_fleet.parse_planner_output(SAMPLE_OUTPUT_WITH_GAPS)
    assert spec.covered == 1
    assert spec.total == 3
    assert spec.not_covered == ["src/b.py", "src/c.py"]
    assert spec.is_complete() is False


def test_parse_planner_output_no_domains_raises():
    with pytest.raises(chek_fleet.PlannerOutputError):
        chek_fleet.parse_planner_output("I could not design a fleet, sorry.")


def test_parse_planner_output_missing_summary_is_incomplete_not_a_crash():
    text = "DOMAIN x [1 files, 1 lines]: a.py\nPROMPT:\naudit a.py\n"
    spec = chek_fleet.parse_planner_output(text)
    assert len(spec.domains) == 1
    assert spec.covered is None
    assert spec.is_complete() is False


# ---------------------------------------------------------------------------
# run_fleet_planner
# ---------------------------------------------------------------------------


class _FakeAgenticProvider:
    def __init__(self, final_text):
        self.final_text = final_text
        self.calls = []

    async def run_agentic_task(self, root, system_prompt, user_prompt, *, allowed_tools=None, max_turns=20):
        self.calls.append(
            {"root": root, "system_prompt": system_prompt, "user_prompt": user_prompt, "allowed_tools": allowed_tools}
        )
        return agent_loop.AgentResult(final_text=self.final_text, turns_used=1, hit_turn_limit=False, tool_calls_made=0)


class _FakeProviderWithoutAgentic:
    pass


async def test_run_fleet_planner_calls_provider_with_real_prompt_and_parses(tmp_path):
    provider = _FakeAgenticProvider(SAMPLE_OUTPUT)
    spec = await chek_fleet.run_fleet_planner(provider, tmp_path, REPO_ROOT / "CHEK_PROTOCOL.md")

    assert len(spec.domains) == 2
    call = provider.calls[0]
    assert call["root"] == tmp_path
    assert "audit architect" in call["system_prompt"]
    assert call["allowed_tools"] == agent_loop.READ_ONLY_TOOLS


async def test_run_fleet_planner_raises_when_provider_lacks_support(tmp_path):
    provider = _FakeProviderWithoutAgentic()
    with pytest.raises(chek_fleet.AgenticTaskNotSupportedError):
        await chek_fleet.run_fleet_planner(provider, tmp_path, REPO_ROOT / "CHEK_PROTOCOL.md")


# ---------------------------------------------------------------------------
# get_checker_common_rules / build_checker_prompt
# ---------------------------------------------------------------------------


def test_get_checker_common_rules_from_real_file():
    text = (REPO_ROOT / "CHEK_PROTOCOL.md").read_text(encoding="utf-8")
    rules = chek_fleet.get_checker_common_rules(text)
    assert "Common rules appended to EVERY checker prompt" in rules
    assert "Прочитано: file1, file2" in rules  # this project's report language is Russian


def test_build_checker_prompt_combines_domain_and_common_rules():
    prompt = chek_fleet.build_checker_prompt("audit the core subsystem", "common rule text")
    assert "audit the core subsystem" in prompt
    assert "common rule text" in prompt


def test_build_checker_prompt_includes_suppression_when_given():
    prompt = chek_fleet.build_checker_prompt("audit X", "rules", "ALREADY SETTLED: foo::bar::baz")
    assert "ALREADY SETTLED" in prompt


def test_build_checker_prompt_omits_empty_suppression():
    prompt = chek_fleet.build_checker_prompt("audit X", "rules", "")
    assert prompt.count("\n\n") == 1  # exactly domain + rules, no third empty section


# ---------------------------------------------------------------------------
# parse_checker_output
# ---------------------------------------------------------------------------


def test_parse_checker_output_findings_and_files():
    text = "CRITICAL a.py:10 — race condition — data loss\nHIGH b.py:5 — missing check — crash\nRead: a.py, b.py, c.py\n"
    findings, files_read = chek_fleet.parse_checker_output(text)
    assert len(findings) == 2
    assert findings[0].severity == "CRITICAL"
    assert findings[0].file == "a.py"
    assert findings[0].line == 10
    assert findings[0].description == "race condition — data loss"
    assert files_read == ["a.py", "b.py", "c.py"]


def test_parse_checker_output_clean_report_is_valid():
    findings, files_read = chek_fleet.parse_checker_output("Read: a.py, b.py\n")
    assert findings == []
    assert files_read == ["a.py", "b.py"]


def test_parse_checker_output_russian_severity_words():
    text = "КРИТИЧНО a.py:1 — гонка — потеря данных\nRead: a.py\n"
    findings, _ = chek_fleet.parse_checker_output(text)
    assert findings[0].severity == "КРИТИЧНО"


def test_parse_checker_output_missing_read_line_raises():
    with pytest.raises(chek_fleet.CheckerOutputError):
        chek_fleet.parse_checker_output("CRITICAL a.py:1 — bug — breaks\n")


# ---------------------------------------------------------------------------
# run_checkers
# ---------------------------------------------------------------------------


class _FakeAgenticProviderByMarker:
    """Returns a scripted final_text based on which marker substring appears in the
    system_prompt it was called with — lets one fake stand in for multiple domains."""

    def __init__(self, responses_by_marker: dict[str, str]):
        self.responses_by_marker = responses_by_marker
        self.calls = []

    async def run_agentic_task(self, root, system_prompt, user_prompt, *, allowed_tools=None, max_turns=20):
        self.calls.append({"system_prompt": system_prompt, "allowed_tools": allowed_tools})
        for marker, text in self.responses_by_marker.items():
            if marker in system_prompt:
                return agent_loop.AgentResult(final_text=text, turns_used=1, hit_turn_limit=False, tool_calls_made=0)
        raise AssertionError(f"no scripted response for prompt containing: {system_prompt[:80]!r}")


def _two_domain_spec():
    return chek_fleet.FleetSpec(
        domains=[
            chek_fleet.DomainSpec(name="core", files=["a.py", "b.py"], prompt="MARKER_CORE: audit the core"),
            chek_fleet.DomainSpec(name="contract", files=["c.py"], prompt="MARKER_CONTRACT: audit the contract"),
        ],
        covered=3,
        total=3,
        not_covered=[],
    )


async def test_run_checkers_dispatches_per_domain_and_aggregates(tmp_path):
    provider = _FakeAgenticProviderByMarker(
        {
            "MARKER_CORE": "CRITICAL a.py:1 — bug — breaks\nRead: a.py, b.py\n",
            "MARKER_CONTRACT": "Read: c.py\n",
        }
    )
    reports = await chek_fleet.run_checkers(provider, tmp_path, _two_domain_spec(), REPO_ROOT / "CHEK_PROTOCOL.md")

    assert set(reports) == {"core", "contract"}
    assert len(reports["core"].findings) == 1
    assert reports["core"].files_read == ["a.py", "b.py"]
    assert reports["contract"].findings == []
    # each checker got the common rules appended to its OWN domain prompt
    core_call = next(c for c in provider.calls if "MARKER_CORE" in c["system_prompt"])
    assert "Common rules appended to EVERY checker prompt" in core_call["system_prompt"]
    assert core_call["allowed_tools"] == agent_loop.READ_ONLY_TOOLS


async def test_run_checkers_runs_domains_concurrently(tmp_path):
    barrier = asyncio.Barrier(2)

    class _BarrierProvider:
        async def run_agentic_task(self, root, system_prompt, user_prompt, *, allowed_tools=None, max_turns=20):
            await barrier.wait()
            return agent_loop.AgentResult(final_text="Read: x.py\n", turns_used=1, hit_turn_limit=False, tool_calls_made=0)

    await asyncio.wait_for(
        chek_fleet.run_checkers(_BarrierProvider(), tmp_path, _two_domain_spec(), REPO_ROOT / "CHEK_PROTOCOL.md"),
        timeout=2,
    )


async def test_run_checkers_unparseable_output_recorded_not_dropped(tmp_path):
    provider = _FakeAgenticProviderByMarker(
        {
            "MARKER_CORE": "no Read line at all, malformed output",
            "MARKER_CONTRACT": "Read: c.py\n",
        }
    )
    reports = await chek_fleet.run_checkers(provider, tmp_path, _two_domain_spec(), REPO_ROOT / "CHEK_PROTOCOL.md")
    # "core" failed to parse -> still present in the aggregation (never silently
    # dropped), flagged via .error; "contract" ran fine alongside it.
    assert "core" in reports
    assert reports["core"].error is not None
    assert reports["core"].files_read == []
    assert "contract" in reports
    assert reports["contract"].error is None


async def test_run_checkers_raises_when_provider_lacks_support(tmp_path):
    with pytest.raises(chek_fleet.AgenticTaskNotSupportedError):
        await chek_fleet.run_checkers(
            _FakeProviderWithoutAgentic(), tmp_path, _two_domain_spec(), REPO_ROOT / "CHEK_PROTOCOL.md"
        )
