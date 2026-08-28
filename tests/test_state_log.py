from __future__ import annotations

from app.registry_store.state_log import append_entry, read_tail


def test_append_entry_creates_file_with_header(tmp_path):
    append_entry(tmp_path, "FIX", {"area": "auth", "result": "fixed null check"})

    text = (tmp_path / "STATE_LOG.md").read_text(encoding="utf-8")
    assert "STATE_LOG" in text
    assert "--- [FIX]" in text
    assert "МСК" in text
    assert "UTC" in text
    assert "area: auth" in text
    assert "result: fixed null check" in text


def test_append_entry_appends_without_touching_previous(tmp_path):
    append_entry(tmp_path, "STATE", {"a": "1"})
    append_entry(tmp_path, "FIX", {"b": "2"})

    text = (tmp_path / "STATE_LOG.md").read_text(encoding="utf-8")
    assert "--- [STATE]" in text
    assert "--- [FIX]" in text
    assert text.index("--- [STATE]") < text.index("--- [FIX]")


def test_read_tail_missing_file_returns_empty(tmp_path):
    assert read_tail(tmp_path) == ""


def test_read_tail_returns_only_last_n_lines(tmp_path):
    for i in range(50):
        append_entry(tmp_path, "STATE", {"i": str(i)})

    tail = read_tail(tmp_path, max_lines=5)
    assert tail.count("\n") <= 4
    assert "i: 49" in tail
    assert "i: 0" not in tail
