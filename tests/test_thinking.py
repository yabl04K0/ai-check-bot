from __future__ import annotations

import pytest

from app.providers.thinking import (
    LEVELS,
    set_thinking_level,
    thinking_instruction,
    thinking_level,
)


def test_thinking_level_defaults_to_off_when_unset(db):
    assert thinking_level() == "off"


@pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
def test_set_thinking_level_persists_and_is_read_back(db, level):
    set_thinking_level(level)
    assert thinking_level() == level


def test_set_thinking_level_updates_existing_row(db):
    set_thinking_level("low")
    assert thinking_level() == "low"

    set_thinking_level("high")
    assert thinking_level() == "high"


def test_set_thinking_level_invalid_raises_value_error(db):
    with pytest.raises(ValueError):
        set_thinking_level("ultrathink")


def test_thinking_instruction_returns_none_for_off(db):
    set_thinking_level("off")
    assert thinking_instruction() is None


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_thinking_instruction_returns_text_for_low_medium_high(db, level):
    set_thinking_level(level)
    instruction = thinking_instruction()
    assert isinstance(instruction, str)
    assert instruction


def test_thinking_instruction_uses_current_stored_level_when_not_passed(db):
    set_thinking_level("medium")
    assert thinking_instruction() == thinking_instruction("medium")


def test_thinking_instruction_explicit_level_overrides_stored_level(db):
    set_thinking_level("off")
    assert thinking_instruction("high") == thinking_instruction(level="high")
    assert thinking_instruction("high") is not None


def test_thinking_instruction_explicit_off_returns_none_regardless_of_stored_level(db):
    set_thinking_level("high")
    assert thinking_instruction("off") is None


def test_thinking_instruction_unknown_explicit_level_returns_none(db):
    assert thinking_instruction("does_not_exist") is None


def test_all_levels_constant_matches_documented_set(db):
    assert LEVELS == ("off", "low", "medium", "high")


def test_low_medium_high_instructions_are_distinct(db):
    texts = {level: thinking_instruction(level) for level in ("low", "medium", "high")}
    assert len(set(texts.values())) == 3
