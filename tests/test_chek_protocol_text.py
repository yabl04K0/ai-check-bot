from pathlib import Path

import pytest

from ai_check_bot import chek_protocol_text as pt

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """\
Some preamble text, not part of any section.

# ============================================================================
# STEP 1 — problem registry
# ============================================================================

Step 1 body text.

# ============================================================================
# STEP 2 — tests
# ============================================================================

Step 2 body with a prompt:
```
You are a tester.
Do the thing.
```
And more text after.
"""


def test_load_sections_splits_by_header():
    sections = pt.load_sections(SAMPLE)
    assert set(sections) == {"STEP 1 — problem registry", "STEP 2 — tests"}


def test_section_body_excludes_the_next_header():
    sections = pt.load_sections(SAMPLE)
    assert "STEP 1" not in sections["STEP 2 — tests"]


def test_find_section_matches_by_prefix():
    sections = pt.load_sections(SAMPLE)
    body = pt.find_section(sections, "STEP 2")
    assert "prompt" in body


def test_find_section_missing_raises():
    sections = pt.load_sections(SAMPLE)
    with pytest.raises(pt.SectionNotFoundError):
        pt.find_section(sections, "STEP 99")


def test_find_section_does_not_confuse_step_1_with_step_10_11_12_13(real_sections):
    body = pt.find_section(real_sections, "STEP 1")
    assert "chek_open.md" in body
    assert "MAX_GLOBAL" not in body  # unique to Step 11 — would appear if the prefix bug returned it instead


def test_find_section_word_boundary_regardless_of_dict_order():
    # The real file's natural order (Step 1 before Step 10) would make a bare
    # .startswith() bug pass by luck even on real data — dict insertion order alone
    # happens to protect it. This test removes that luck: "STEP 10" is inserted BEFORE
    # "STEP 1", so only a real word-boundary check (not .startswith("STEP 1")) can
    # return the correct section.
    adversarial = {
        "STEP 10 — critics": "critics body",
        "STEP 11 — convergence": "convergence body",
        "STEP 1 — problem registry": "registry body",
    }
    assert pt.find_section(adversarial, "STEP 1") == "registry body"
    assert pt.find_section(adversarial, "STEP 10") == "critics body"


def test_extract_fenced_blocks_finds_one_block():
    sections = pt.load_sections(SAMPLE)
    blocks = pt.extract_fenced_blocks(sections["STEP 2 — tests"])
    assert len(blocks) == 1
    assert blocks[0] == "You are a tester.\nDo the thing."


def test_extract_fenced_blocks_empty_for_step_with_none():
    sections = pt.load_sections(SAMPLE)
    assert pt.extract_fenced_blocks(sections["STEP 1 — problem registry"]) == []


# ---------------------------------------------------------------------------
# against the REAL CHEK_PROTOCOL.md — the actual thing this module must parse
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_sections():
    text = (REPO_ROOT / "CHEK_PROTOCOL.md").read_text(encoding="utf-8")
    return pt.load_sections(text)


@pytest.mark.parametrize(
    "step_prefix,expected_block_count",
    [
        ("STEP 5", 1),   # fleet planner prompt
        ("STEP 8", 1),   # gap-finder prompt
        ("STEP 9", 1),   # fixer prompt
        ("STEP 10", 2),  # critic A + critic B
        ("STEP 11", 3),  # pseudocode loop + intermediate verifier + follow-up fixer
        ("STEP 12", 1),  # test-writer prompt
    ],
)
def test_real_protocol_step_block_counts(real_sections, step_prefix, expected_block_count):
    body = pt.find_section(real_sections, step_prefix)
    blocks = pt.extract_fenced_blocks(body)
    assert len(blocks) == expected_block_count


def test_real_protocol_step5_prompt_looks_right(real_sections):
    body = pt.find_section(real_sections, "STEP 5")
    prompt = pt.extract_fenced_blocks(body)[0]
    assert "audit architect" in prompt
    assert "DOMAIN <name>" in prompt
