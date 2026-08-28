from __future__ import annotations

from app.registry_store.project_memory import append_session_log_entry, read_architecture


def test_read_architecture_missing_file_returns_empty(tmp_path):
    assert read_architecture(tmp_path) == ""


def test_read_architecture_excludes_session_log_tail(tmp_path):
    (tmp_path / "PROJECT_MEMORY.md").write_text(
        "# STRUCTURE\nsome architecture notes\n\n"
        "# ============================\n# SESSION LOG\n# ============================\n\n"
        "--- 2026-08-01 entry one ---\nold stuff\n",
        encoding="utf-8",
    )

    text = read_architecture(tmp_path)

    assert "architecture notes" in text
    assert "old stuff" not in text
    assert "SESSION LOG" not in text


def test_read_architecture_returns_whole_file_when_no_marker(tmp_path):
    (tmp_path / "PROJECT_MEMORY.md").write_text("just architecture, no log yet", encoding="utf-8")

    assert read_architecture(tmp_path) == "just architecture, no log yet"


def test_append_session_log_entry_returns_false_when_file_missing(tmp_path):
    assert append_session_log_entry(tmp_path, "title", "body") is False


def test_append_session_log_entry_appends_when_file_exists(tmp_path):
    (tmp_path / "PROJECT_MEMORY.md").write_text("# SESSION LOG\n\n--- old entry ---\nold\n", encoding="utf-8")

    ok = append_session_log_entry(tmp_path, "bot ran FIX #5", "did the thing")

    assert ok is True
    text = (tmp_path / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    assert "old entry" in text  # старое не тронуто
    assert "bot ran FIX #5" in text
    assert "did the thing" in text
    assert text.index("old entry") < text.index("bot ran FIX #5")  # новое строго после старого
