from pathlib import Path

import pytest

from ai_check_bot import agent_loop, chek_fleet

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
