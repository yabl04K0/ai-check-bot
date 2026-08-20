"""start_job() — единственный настоящий "старт выполнения" во всей
кодовой базе (см. app/bot/job_runner.py) — теперь гейтится
job_needs_manual_approval(): пока включён доступ ИИ к GITHUB_TOKEN и
выключено автоодобрение, задача не запускает пайплайн сама, а шлёт
запрос на подтверждение и остаётся QUEUED, пока человек не тапнет
"✅ Разрешить" (что добавляет job_id в APPROVED_JOB_IDS и вызывает
start_job() ещё раз)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.bot.job_runner as job_runner_module
from app.db.models import Job, JobStatus, TaskType
from app.db.session import get_session
from app.providers.ai_autonomy import set_ai_command_auto_approve, set_ai_github_token_access


def _run(coro):
    return asyncio.run(coro)


def _make_job(session, *, created_by_tg_id: int | None = 555) -> int:
    job = Job(
        task_type=TaskType.FIX,
        status=JobStatus.QUEUED,
        created_by_tg_id=created_by_tg_id,
        progress_total=1,
    )
    session.add(job)
    session.flush()
    return job.id


def _fake_application():
    send_message = AsyncMock()
    return SimpleNamespace(bot=SimpleNamespace(send_message=send_message)), send_message


def _fake_pipeline_marks_done(application, job_id):
    with get_session() as session:
        job = session.get(Job, job_id)
        job.status = JobStatus.DONE
        job.report_text = "ok"
    return {}


def test_blocks_and_sends_approval_request_when_needed(db):
    with get_session() as session:
        job_id = _make_job(session)
    set_ai_github_token_access(True)
    application, send_message = _fake_application()

    _run(job_runner_module.start_job(application, job_id))

    send_message.assert_awaited_once()
    args, kwargs = send_message.await_args
    assert args[0] == 555
    assert "GITHUB_TOKEN" in args[1]
    assert kwargs["reply_markup"] is not None

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.QUEUED


def test_proceeds_normally_when_no_approval_needed(db, monkeypatch):
    with get_session() as session:
        job_id = _make_job(session)
    application, send_message = _fake_application()
    monkeypatch.setattr(job_runner_module, "_run_pipeline_blocking", _fake_pipeline_marks_done)
    monkeypatch.setattr(job_runner_module, "_progress_loop", AsyncMock())

    _run(job_runner_module.start_job(application, job_id))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.DONE
    first_call_args = send_message.await_args_list[0].args
    assert "GITHUB_TOKEN" not in first_call_args[1]


def test_proceeds_when_auto_approve_also_on(db, monkeypatch):
    with get_session() as session:
        job_id = _make_job(session)
    set_ai_github_token_access(True)
    set_ai_command_auto_approve(True)
    application, send_message = _fake_application()
    monkeypatch.setattr(job_runner_module, "_run_pipeline_blocking", _fake_pipeline_marks_done)
    monkeypatch.setattr(job_runner_module, "_progress_loop", AsyncMock())

    _run(job_runner_module.start_job(application, job_id))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.DONE


def test_approved_job_id_bypasses_gate_once(db, monkeypatch):
    """Эмулирует тап "✅ Разрешить" (см. app.bot.handlers.check.approve_job_start):
    job_id заранее в APPROVED_JOB_IDS — гейт пропускает выполнение и сам
    же вычищает id из набора, чтобы следующий отдельный запуск снова
    требовал подтверждения."""
    with get_session() as session:
        job_id = _make_job(session)
    set_ai_github_token_access(True)
    job_runner_module.APPROVED_JOB_IDS.add(job_id)
    application, send_message = _fake_application()
    monkeypatch.setattr(job_runner_module, "_run_pipeline_blocking", _fake_pipeline_marks_done)
    monkeypatch.setattr(job_runner_module, "_progress_loop", AsyncMock())

    _run(job_runner_module.start_job(application, job_id))

    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.DONE
    assert job_id not in job_runner_module.APPROVED_JOB_IDS


def test_does_not_start_second_job_while_another_is_already_running(db, monkeypatch):
    """Регрессия: пока job A ждёт подтверждения (QUEUED, is_busy()==False,
    т.к. is_busy считает только RUNNING/PAUSED_MANUAL), человек мог тем
    временем подтвердить/enqueue-нуть job B, которая реально стартовала
    (RUNNING). Если после этого пришло подтверждение на A, start_job(A)
    не должен молча пометить её RUNNING поверх уже выполняющейся B —
    должен оставить A в QUEUED и выйти, доверяя обычному дренажу очереди
    (хвост start_job после завершения B)."""
    with get_session() as session:
        job_a = _make_job(session)
        job_b = Job(task_type=TaskType.FIX, status=JobStatus.RUNNING, progress_total=1)
        session.add(job_b)
        session.flush()
        job_b_id = job_b.id

    set_ai_github_token_access(True)
    job_runner_module.APPROVED_JOB_IDS.add(job_a)
    application, send_message = _fake_application()
    monkeypatch.setattr(job_runner_module, "_run_pipeline_blocking", _fake_pipeline_marks_done)
    monkeypatch.setattr(job_runner_module, "_progress_loop", AsyncMock())

    _run(job_runner_module.start_job(application, job_a))

    with get_session() as session:
        assert session.get(Job, job_a).status == JobStatus.QUEUED
        assert session.get(Job, job_b_id).status == JobStatus.RUNNING
    send_message.assert_not_awaited()


def test_job_without_chat_id_errors_instead_of_hanging_forever(db):
    """Задача без chat_id (нет живого юзера, которому показать запрос) не
    должна тихо зависать в QUEUED навсегда — явная ошибка понятнее."""
    with get_session() as session:
        job_id = _make_job(session, created_by_tg_id=None)
    set_ai_github_token_access(True)
    application, send_message = _fake_application()

    _run(job_runner_module.start_job(application, job_id))

    send_message.assert_not_awaited()
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.status == JobStatus.ERROR
