from __future__ import annotations

from app.ai_chat import agent_activity


def test_start_returns_incrementing_ids():
    first = agent_activity.start("demo", "task one")
    second = agent_activity.start("demo", "task two")

    assert second != first
    agent_activity.finish(first)
    agent_activity.finish(second)


def test_start_and_active_round_trip():
    activity_id = agent_activity.start("demo", "почини баг")

    entries = agent_activity.active()
    assert len(entries) == 1
    assert entries[0].id == activity_id
    assert entries[0].project == "demo"
    assert entries[0].task == "почини баг"

    agent_activity.finish(activity_id)
    assert agent_activity.active() == []


def test_finish_removes_only_matching_entry():
    first = agent_activity.start("demo-a", "task a")
    second = agent_activity.start("demo-b", "task b")

    agent_activity.finish(first)

    entries = agent_activity.active()
    assert len(entries) == 1
    assert entries[0].id == second

    agent_activity.finish(second)


def test_multiple_concurrent_entries_all_visible():
    ids = [agent_activity.start(f"proj-{i}", f"task {i}") for i in range(5)]

    entries = agent_activity.active()
    assert {e.id for e in entries} == set(ids)
    assert {e.project for e in entries} == {f"proj-{i}" for i in range(5)}

    for activity_id in ids:
        agent_activity.finish(activity_id)
    assert agent_activity.active() == []


def test_finish_on_unknown_id_is_safe_no_op():
    agent_activity.finish(999999)
    assert agent_activity.active() == []


def test_finish_twice_is_safe_no_op():
    activity_id = agent_activity.start("demo", "task")
    agent_activity.finish(activity_id)
    agent_activity.finish(activity_id)
    assert agent_activity.active() == []


def test_elapsed_seconds_computed_from_monotonic(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(agent_activity.time, "monotonic", lambda: clock[0])

    activity_id = agent_activity.start("demo", "task")
    clock[0] = 1042.0

    entries = agent_activity.active()
    assert entries[0].elapsed_seconds() == 42.0

    agent_activity.finish(activity_id)
