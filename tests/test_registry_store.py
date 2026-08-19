from __future__ import annotations

from app.registry_store.store import (
    Registry,
    RegistryFinding,
    move_finding,
    read_registry,
    write_registry,
)


def test_write_then_read_roundtrip(tmp_path):
    registry = Registry(
        open=[
            RegistryFinding(
                file_symbol="app/auth.py::validate_token",
                description="Токен не проверяется на None.",
                severity="critical",
                attempts=2,
            )
        ],
        later=[RegistryFinding(file_symbol="app/x.py::y", description="", reason="занят другим")],
        never=[RegistryFinding(file_symbol="app/z.py::w", description="", reason="не баг")],
    )
    write_registry(tmp_path, registry)

    loaded = read_registry(tmp_path)
    assert len(loaded.open) == 1
    assert loaded.open[0].file_symbol == "app/auth.py::validate_token"
    assert loaded.open[0].severity == "critical"
    assert loaded.open[0].attempts == 2
    assert "None" in loaded.open[0].description

    assert len(loaded.later) == 1
    assert loaded.later[0].reason == "занят другим"
    assert len(loaded.never) == 1


def test_read_registry_missing_files_returns_empty(tmp_path):
    registry = read_registry(tmp_path)
    assert registry.open == []
    assert registry.later == []
    assert registry.never == []


def test_move_finding_open_to_later(tmp_path):
    registry = Registry(
        open=[RegistryFinding(file_symbol="a.py::f", description="desc", severity="high")]
    )
    write_registry(tmp_path, registry)

    moved = move_finding(tmp_path, "a.py::f", to="later", reason="занят другим")
    assert moved is True

    loaded = read_registry(tmp_path)
    assert loaded.open == []
    assert len(loaded.later) == 1
    assert loaded.later[0].reason == "занят другим"
    # severity сохраняется даже после переноса в later
    assert loaded.later[0].severity == "high"


def test_move_finding_not_found_returns_false(tmp_path):
    assert move_finding(tmp_path, "nope::nope", to="never") is False
