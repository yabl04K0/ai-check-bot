from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentActivity:
    id: int
    project: str
    task: str
    started_at: float

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at


_lock = threading.Lock()
_entries: dict[int, AgentActivity] = {}
_next_id = 1


def start(project: str, task: str) -> int:
    global _next_id
    with _lock:
        activity_id = _next_id
        _next_id += 1
        _entries[activity_id] = AgentActivity(
            id=activity_id, project=project, task=task, started_at=time.monotonic()
        )
    return activity_id


def finish(activity_id: int) -> None:
    with _lock:
        _entries.pop(activity_id, None)


def active() -> list[AgentActivity]:
    with _lock:
        return sorted(_entries.values(), key=lambda entry: entry.id)
