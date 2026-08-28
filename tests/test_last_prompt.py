from __future__ import annotations

from app.registry_store.last_prompt import read_last_prompt, write_last_prompt


def test_read_missing_file_returns_empty(tmp_path):
    assert read_last_prompt(tmp_path) == ""


def test_write_then_read_roundtrip(tmp_path):
    write_last_prompt(tmp_path, "Continue from step 3")
    assert read_last_prompt(tmp_path) == "Continue from step 3"


def test_write_strips_surrounding_whitespace(tmp_path):
    write_last_prompt(tmp_path, "  padded text  \n\n")
    assert read_last_prompt(tmp_path) == "padded text"


def test_write_overwrites_previous_content(tmp_path):
    write_last_prompt(tmp_path, "first")
    write_last_prompt(tmp_path, "second")
    assert read_last_prompt(tmp_path) == "second"
