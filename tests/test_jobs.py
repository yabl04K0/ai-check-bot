import asyncio

import pytest

from ai_check_bot import jobs


def test_create_job_starts_all_pending():
    job = jobs.create_job("t", ["a", "b"])
    assert job.active_count() == 0
    assert job.done_count() == 0
    assert set(job.workers) == {"a", "b"}
    jobs.drop_job(job.id)


async def test_run_workers_success_and_failure():
    job = jobs.create_job("t", ["ok", "bad"])
    progress_calls = []

    async def run_one(name):
        if name == "bad":
            raise RuntimeError("boom")
        return "done-detail"

    async def on_progress(j):
        progress_calls.append((j.workers["ok"].state, j.workers["bad"].state))

    await jobs.run_workers(job, ["ok", "bad"], run_one, on_progress=on_progress)

    assert job.workers["ok"].state == "done"
    assert job.workers["ok"].detail == "done-detail"
    assert job.workers["bad"].state == "failed"
    assert "boom" in job.workers["bad"].detail
    assert job.done_count() == 2
    assert len(progress_calls) == 4  # running+done/failed per worker
    jobs.drop_job(job.id)


async def test_cancel_stops_remaining_workers():
    job = jobs.create_job("t", ["first", "second", "third"])
    seen = []

    async def run_one(name):
        seen.append(name)
        job.cancel_requested = True  # simulate a cancel arriving mid-run
        return "ok"

    async def on_progress(j):
        pass

    await jobs.run_workers(job, ["first", "second", "third"], run_one, on_progress=on_progress)

    assert seen == ["first"]
    assert job.workers["first"].state == "done"
    assert job.workers["second"].state == "cancelled"
    assert job.workers["third"].state == "cancelled"
    jobs.drop_job(job.id)


async def test_run_workers_parallel_actually_runs_concurrently():
    # If this ran sequentially, worker "b" could not finish before "a" starts waiting —
    # but here both reach the barrier and only THEN proceed, which is only possible if
    # they are genuinely running at the same time, not one after another.
    job = jobs.create_job("t", ["a", "b"])
    barrier = asyncio.Barrier(2)

    async def run_one(name):
        await barrier.wait()
        return "ok"

    async def on_progress(j):
        pass

    await asyncio.wait_for(
        jobs.run_workers_parallel(job, ["a", "b"], run_one, on_progress=on_progress), timeout=2
    )
    assert job.workers["a"].state == "done"
    assert job.workers["b"].state == "done"
    jobs.drop_job(job.id)


async def test_run_workers_parallel_isolates_failures():
    job = jobs.create_job("t", ["ok", "bad"])

    async def run_one(name):
        if name == "bad":
            raise RuntimeError("boom")
        return "fine"

    async def on_progress(j):
        pass

    await jobs.run_workers_parallel(job, ["ok", "bad"], run_one, on_progress=on_progress)
    assert job.workers["ok"].state == "done"
    assert job.workers["bad"].state == "failed"
    assert "boom" in job.workers["bad"].detail
    jobs.drop_job(job.id)


async def test_run_workers_parallel_pre_cancelled_skips_all():
    job = jobs.create_job("t", ["a", "b"])
    job.cancel_requested = True
    started = []

    async def run_one(name):
        started.append(name)
        return "ok"

    async def on_progress(j):
        pass

    await jobs.run_workers_parallel(job, ["a", "b"], run_one, on_progress=on_progress)
    assert started == []
    assert job.workers["a"].state == "cancelled"
    assert job.workers["b"].state == "cancelled"
    jobs.drop_job(job.id)


def test_push_interjection_unknown_job_returns_false():
    assert jobs.push_interjection("does-not-exist", "hi") is False


def test_request_cancel_unknown_job_returns_false():
    assert jobs.request_cancel("does-not-exist") is False


async def test_attach_task_and_request_cancel_actually_cancels():
    job = jobs.create_job("t", ["only"])
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(10)
        return "should not get here"

    task = asyncio.ensure_future(slow())
    jobs.attach_task(job.id, task)
    await started.wait()

    assert jobs.request_cancel(job.id) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    jobs.drop_job(job.id)


async def test_debounced_editor_collapses_rapid_calls():
    calls = []

    async def edit_fn(text):
        calls.append(text)

    editor = await jobs.debounced_editor(edit_fn, min_interval=0.05)
    await asyncio.gather(editor("a"), editor("b"))
    assert calls == ["a", "b"]  # both applied, in order, just not concurrently
