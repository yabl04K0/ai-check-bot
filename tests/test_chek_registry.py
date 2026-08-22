import shutil
from pathlib import Path

import pytest

from ai_check_bot import chek_registry as reg

REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_registry(tmp_path, name):
    dst = tmp_path / name
    shutil.copy(REPO_ROOT / name, dst)
    return dst


def test_parse_real_open_file_is_empty_placeholder():
    text = (REPO_ROOT / "chek_open.md").read_text(encoding="utf-8")
    assert reg.parse(text, "open") == []


def test_parse_real_never_file_is_empty_placeholder():
    text = (REPO_ROOT / "chek_never.md").read_text(encoding="utf-8")
    assert reg.parse(text, "never") == []


def test_parse_real_later_file_is_empty_placeholder():
    text = (REPO_ROOT / "chek_later.md").read_text(encoding="utf-8")
    assert reg.parse(text, "later") == []


def test_wrong_kind_raises_format_error():
    text = (REPO_ROOT / "chek_open.md").read_text(encoding="utf-8")
    with pytest.raises(reg.RegistryFormatError):
        reg.parse(text, "never")


def test_zero_valued_fields_are_actually_written_not_just_defaulted_back():
    # passes_run=0 must appear in the serialized YAML text itself — re-parsing a value
    # that was silently DROPPED would still show 0 (the dataclass default), so a test
    # that only checks the parsed-back value would not catch a truthiness-filter bug
    # (`if v` instead of `if v not in (None, [], "")`) that drops legitimate zeros.
    block = reg._render_block([reg.OpenEntry(id="a::b::c", severity="HIGH", passes_run=0, first_seen="x")])
    assert "passes_run: 0" in block


def test_append_then_parse_roundtrips(tmp_path):
    path = _copy_registry(tmp_path, "chek_open.md")
    entry = reg.OpenEntry(
        id="foo.py::bar::pattern",
        severity="HIGH",
        status="open",
        passes_run=0,
        passes_life=1,
        first_seen="2026-08-21 12:00",
    )
    reg.append_entry(path, "open", entry)

    entries = reg.parse(path.read_text(encoding="utf-8"), "open")
    assert len(entries) == 1
    assert entries[0].id == "foo.py::bar::pattern"
    assert entries[0].severity == "HIGH"


def test_append_duplicate_id_raises(tmp_path):
    path = _copy_registry(tmp_path, "chek_open.md")
    entry = reg.OpenEntry(id="dup::x::y", severity="MEDIUM", first_seen="2026-08-21 12:00")
    reg.append_entry(path, "open", entry)
    with pytest.raises(ValueError):
        reg.append_entry(path, "open", entry)


def test_append_two_entries_both_survive(tmp_path):
    path = _copy_registry(tmp_path, "chek_open.md")
    reg.append_entry(path, "open", reg.OpenEntry(id="a::b::c", severity="MEDIUM", first_seen="x"))
    reg.append_entry(path, "open", reg.OpenEntry(id="d::e::f", severity="HIGH", first_seen="y"))
    entries = reg.parse(path.read_text(encoding="utf-8"), "open")
    assert {e.id for e in entries} == {"a::b::c", "d::e::f"}


def test_remove_entry_true_when_present(tmp_path):
    path = _copy_registry(tmp_path, "chek_open.md")
    reg.append_entry(path, "open", reg.OpenEntry(id="gone::x::y", severity="MEDIUM", first_seen="x"))
    assert reg.remove_entry(path, "open", "gone::x::y") is True
    assert reg.parse(path.read_text(encoding="utf-8"), "open") == []


def test_remove_entry_false_when_absent(tmp_path):
    path = _copy_registry(tmp_path, "chek_open.md")
    assert reg.remove_entry(path, "open", "does-not-exist") is False


def test_never_entry_roundtrip(tmp_path):
    path = _copy_registry(tmp_path, "chek_never.md")
    entry = reg.NeverEntry(id="x.py::f::pat", severity="MEDIUM", reason="not a bug", added="2026-08-21")
    reg.append_entry(path, "never", entry)
    entries = reg.parse(path.read_text(encoding="utf-8"), "never")
    assert entries[0].reason == "not a bug"


def test_later_entry_optional_fields_omitted_when_unset(tmp_path):
    path = _copy_registry(tmp_path, "chek_later.md")
    entry = reg.LaterEntry(id="y.py::g::pat", severity="MEDIUM", deferred_reason="low priority", first_seen="2026-08-21 12:00")
    reg.append_entry(path, "later", entry)
    text = path.read_text(encoding="utf-8")
    _, _, entries_block = reg._entries_block(text, "later")  # only the entries region — the
    assert "deferred_sha" not in entries_block  # file header legitimately documents both field names
    assert "remind_when" not in entries_block
    entries = reg.parse(text, "later")
    assert entries[0].deferred_sha is None


def test_later_entry_optional_fields_kept_when_set(tmp_path):
    path = _copy_registry(tmp_path, "chek_later.md")
    entry = reg.LaterEntry(
        id="z.py::h::pat",
        severity="MEDIUM",
        deferred_reason="waiting",
        first_seen="2026-08-21 12:00",
        deferred_sha="abc123",
        remind_when="module touched again",
    )
    reg.append_entry(path, "later", entry)
    entries = reg.parse(path.read_text(encoding="utf-8"), "later")
    assert entries[0].deferred_sha == "abc123"
    assert entries[0].remind_when == "module touched again"


def test_check_duplicate_ids_detects_cross_file_duplicate(tmp_path):
    open_path = _copy_registry(tmp_path, "chek_open.md")
    never_path = _copy_registry(tmp_path, "chek_never.md")
    later_path = _copy_registry(tmp_path, "chek_later.md")

    dup_id = "leaky.py::run::race"
    reg.append_entry(open_path, "open", reg.OpenEntry(id=dup_id, severity="HIGH", first_seen="x"))
    reg.append_entry(never_path, "never", reg.NeverEntry(id=dup_id, severity="HIGH", reason="dup by mistake", added="2026-08-21"))
    reg.append_entry(open_path, "open", reg.OpenEntry(id="clean::a::b", severity="MEDIUM", first_seen="x"))

    dupes = reg.check_duplicate_ids(open_path, never_path, later_path)
    assert dupes == {dup_id: ["open", "never"]}


def test_check_duplicate_ids_clean_registries_report_nothing(tmp_path):
    open_path = _copy_registry(tmp_path, "chek_open.md")
    never_path = _copy_registry(tmp_path, "chek_never.md")
    later_path = _copy_registry(tmp_path, "chek_later.md")
    assert reg.check_duplicate_ids(open_path, never_path, later_path) == {}


def test_attempts_and_subfindings_roundtrip(tmp_path):
    path = _copy_registry(tmp_path, "chek_open.md")
    entry = reg.OpenEntry(
        id="w.py::f::pat",
        severity="CRITICAL",
        status="open",
        passes_run=1,
        passes_life=2,
        first_seen="2026-08-21 12:00",
        attempts=["tried X -> reviewer found Y"],
        subfindings=["detail A", "detail B"],
    )
    reg.append_entry(path, "open", entry)
    entries = reg.parse(path.read_text(encoding="utf-8"), "open")
    assert entries[0].attempts == ["tried X -> reviewer found Y"]
    assert entries[0].subfindings == ["detail A", "detail B"]
