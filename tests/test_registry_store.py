from __future__ import annotations

from app.registry_store.store import (
    Registry,
    RegistryFinding,
    move_finding,
    read_registry,
    register_or_bump_finding,
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


def test_register_or_bump_new_finding(tmp_path):
    outcome = register_or_bump_finding(
        tmp_path, RegistryFinding(file_symbol="a.py::f", description="сломано", severity="high")
    )
    assert outcome == "new"
    assert len(read_registry(tmp_path).open) == 1


def test_register_or_bump_existing_open_bumps_attempts(tmp_path):
    existing = RegistryFinding(file_symbol="a.py::f", description="old", severity="medium", attempts=1)
    write_registry(tmp_path, Registry(open=[existing]))
    outcome = register_or_bump_finding(
        tmp_path, RegistryFinding(file_symbol="a.py::f", description="new desc", severity="critical")
    )
    assert outcome == "bumped"
    loaded = read_registry(tmp_path)
    assert len(loaded.open) == 1
    assert loaded.open[0].attempts == 2
    assert loaded.open[0].severity == "critical"
    assert loaded.open[0].description == "new desc"


def test_register_or_bump_respects_deferred_by_default(tmp_path):
    """Скоуп по умолчанию ("всё") уважает Отложено/Never — не переоткрывает."""
    write_registry(
        tmp_path,
        Registry(never=[RegistryFinding(file_symbol="a.py::f", description="дизайн такой", reason="не баг")]),
    )
    outcome = register_or_bump_finding(
        tmp_path, RegistryFinding(file_symbol="a.py::f", description="снова нашли", severity="low")
    )
    assert outcome == "deferred_skipped"
    loaded = read_registry(tmp_path)
    assert loaded.open == []
    assert len(loaded.never) == 1  # не тронуто


def test_register_or_bump_ignore_deferred_moves_back_to_open(tmp_path):
    """Скоуп "ЧЕК всё" (ignore_deferred=True) переоткрывает даже Never."""
    write_registry(
        tmp_path,
        Registry(later=[RegistryFinding(file_symbol="a.py::f", description="занят", reason="потом")]),
    )
    outcome = register_or_bump_finding(
        tmp_path,
        RegistryFinding(file_symbol="a.py::f", description="актуально снова", severity="high"),
        ignore_deferred=True,
    )
    assert outcome == "moved_from_deferred"
    loaded = read_registry(tmp_path)
    assert loaded.later == []
    assert len(loaded.open) == 1
    assert loaded.open[0].attempts == 1
    assert loaded.open[0].severity == "high"


def test_register_or_bump_catches_reworded_never_duplicate(tmp_path):
    write_registry(
        tmp_path,
        Registry(
            never=[
                RegistryFinding(
                    file_symbol="app/auth.py::validate_token",
                    description="Токен не проверяется на None перед decode и вызывает "
                    "AttributeError при пустом значении.",
                    reason="не баг, decode сам кидает ValueError",
                )
            ]
        ),
    )
    outcome = register_or_bump_finding(
        tmp_path,
        RegistryFinding(
            file_symbol="app/auth.py:validate_token",
            description="Токен не проверяется на None перед decode и вызывает "
            "AttributeError при пустом значении",
        ),
    )
    assert outcome == "deferred_skipped"
    loaded = read_registry(tmp_path)
    assert loaded.open == []
    assert len(loaded.never) == 1


def test_register_or_bump_does_not_merge_unrelated_finding_in_same_file(tmp_path):
    write_registry(
        tmp_path,
        Registry(
            never=[
                RegistryFinding(
                    file_symbol="app/auth.py::validate_token",
                    description="Токен не проверяется на None.",
                    reason="не баг",
                )
            ]
        ),
    )
    outcome = register_or_bump_finding(
        tmp_path,
        RegistryFinding(
            file_symbol="app/auth.py::refresh_session",
            description="Сессия не обновляется при истечении TTL, юзера разлогинивает раньше времени.",
            severity="high",
        ),
    )
    assert outcome == "new"
    loaded = read_registry(tmp_path)
    assert len(loaded.open) == 1
    assert len(loaded.never) == 1
