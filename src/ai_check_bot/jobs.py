"""Generic in-flight job: live status (how many workers, which state each is in), a
cancel flag checked between workers, and a queue for messages sent WHILE the job runs.

Scope note: this is infrastructure, not the CHEK fleet itself. The bot's real audit
fleet (README "Режимы аудита") is not implemented yet — this module is the reusable
mechanics that fleet will need (live progress, cancel, mid-run interjection), proven
today on the one concurrent multi-worker action that already exists: probing every
account. Wiring an interjection into an actual running AI agent's context is future
work once a real fleet/task engine exists; today an interjection is recorded and shown
in the status, nothing more.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

_STATE_ICON = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌", "cancelled": "⏹"}


@dataclass
class WorkerStatus:
    name: str
    state: str = "pending"
    detail: str = ""


@dataclass
class Job:
    id: str
    title: str
    workers: dict[str, WorkerStatus]
    cancel_requested: bool = False
    interjections: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    asyncio_task: asyncio.Task | None = None  # set via attach_task for a single long call
    # that can be cancelled mid-flight, unlike the cooperative between-workers check
    # run_workers() uses.

    def active_count(self) -> int:
        return sum(1 for w in self.workers.values() if w.state == "running")

    def done_count(self) -> int:
        return sum(1 for w in self.workers.values() if w.state in ("done", "failed", "cancelled"))

    def render(self) -> str:
        lines = [f"{self.title}", f"{self.done_count()}/{len(self.workers)} завершено · активно: {self.active_count()}"]
        for w in self.workers.values():
            icon = _STATE_ICON.get(w.state, "?")
            suffix = f" — {w.detail}" if w.detail else ""
            lines.append(f"{icon} {w.name}{suffix}")
        if self.interjections:
            lines.append("")
            lines.append("Сообщения во время выполнения (учтены, но пока не передаются в саму задачу):")
            lines.extend(f"· {m}" for m in self.interjections)
        return "\n".join(lines)


_JOBS: dict[str, Job] = {}


def create_job(title: str, worker_names: list[str]) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], title=title, workers={n: WorkerStatus(n) for n in worker_names})
    _JOBS[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def drop_job(job_id: str) -> None:
    _JOBS.pop(job_id, None)


def request_cancel(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if job is None:
        return False
    job.cancel_requested = True
    if job.asyncio_task is not None and not job.asyncio_task.done():
        job.asyncio_task.cancel()
    return True


def attach_task(job_id: str, task: asyncio.Task) -> None:
    """Register the single in-flight asyncio.Task a cancel request should cancel
    directly — for a job that is one long call (run_task), not many short workers."""
    job = _JOBS.get(job_id)
    if job is not None:
        job.asyncio_task = task


def push_interjection(job_id: str, text: str) -> bool:
    job = _JOBS.get(job_id)
    if job is None:
        return False
    job.interjections.append(text)
    return True


async def run_workers(
    job: Job,
    worker_names: list[str],
    run_one,
    *,
    on_progress,
) -> None:
    """Run `run_one(name)` for each worker in order, updating job state and calling
    `on_progress(job)` after every state change so the caller can push a live edit.
    Sequential by design: these calls hit real external APIs, and CHEK_PROTOCOL.md's own
    fleet lesson applies here too — bound concurrency deliberately, don't fire it all at
    once. Stops early (remaining workers marked 'cancelled') if cancel_requested is set."""
    for name in worker_names:
        if job.cancel_requested:
            job.workers[name].state = "cancelled"
            await on_progress(job)
            continue
        job.workers[name].state = "running"
        await on_progress(job)
        try:
            detail = await run_one(name)
            job.workers[name].state = "done"
            job.workers[name].detail = detail or ""
        except Exception as exc:  # a worker failing must not abort the rest of the fleet
            job.workers[name].state = "failed"
            job.workers[name].detail = str(exc)
        await on_progress(job)


async def debounced_editor(edit_fn, *, min_interval: float = 1.0):
    """Wrap a Telegram edit call so rapid worker updates collapse into at most one edit
    per `min_interval` seconds — Telegram rate-limits edit_message_text per chat, and a
    job with many fast workers would otherwise trip it and lose the last few updates."""
    last_call = 0.0
    lock = asyncio.Lock()

    async def _edit(text: str) -> None:
        nonlocal last_call
        async with lock:
            now = time.monotonic()
            wait = min_interval - (now - last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            last_call = time.monotonic()
            await edit_fn(text)

    return _edit
