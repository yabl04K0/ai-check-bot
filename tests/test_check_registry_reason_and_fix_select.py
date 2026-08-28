"""Валидация форматов ввода в app/bot/handlers/check.py::on_text —
раньше состояние (awaiting/registry_job_id/fix_select_job_id) сбрасывалось
ДО проверки формата, так что повторная попытка пользователя (именно то,
что просит подсказка бота) молча терялась: ни одна ветка on_text уже не
совпадала (см. аудит меню)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import check as check_module
from app.db.models import Finding, FindingStatus, Job, Project, TaskType
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _message_update(text: str, user_data: dict):
    reply = AsyncMock()
    message = SimpleNamespace(text=text, reply_text=reply)
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace(
        user_data=user_data,
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(),
    )
    return update, context, reply


def test_fix_select_rejects_empty_text_without_losing_job_id(db):
    update, context, reply = _message_update(
        "   ", {"awaiting": "fix_select", "fix_select_job_id": 42}
    )

    _run(check_module.on_text(update, context))

    reply.assert_awaited_once()
    assert "обязательно" in reply.await_args.args[0]
    # Состояние сохранено — пользователь может прислать текст ещё раз.
    assert context.user_data["awaiting"] == "fix_select"
    assert context.user_data["fix_select_job_id"] == 42


def test_fix_select_accepts_text_and_enqueues(db, monkeypatch):
    with get_session() as session:
        project = Project(name="P", repo_full_name="o/p")
        session.add(project)
        session.flush()
        job = Job(task_type=TaskType.CHECK_FULL, progress_total=1)
        job.projects = [project]
        session.add(job)
        session.flush()
        job_id = job.id

    monkeypatch.setattr(check_module, "start_job", AsyncMock())
    update, context, reply = _message_update(
        "почини баг в auth.py", {"awaiting": "fix_select", "fix_select_job_id": job_id}
    )

    _run(check_module.on_text(update, context))

    with get_session() as session:
        fix_jobs = session.query(Job).filter_by(task_type=TaskType.FIX).all()
        assert len(fix_jobs) == 1
        assert fix_jobs[0].comment == "почини баг в auth.py"
    assert context.user_data["awaiting"] is None
    assert "fix_select_job_id" not in context.user_data


def _make_finding_job(session, local_path: str) -> tuple[int, Project]:
    project = Project(name="P", repo_full_name="o/p", local_path=local_path)
    session.add(project)
    session.flush()
    session.add(
        Finding(project_id=project.id, file_symbol="a.py::foo", description="bug", status=FindingStatus.OPEN)
    )
    job = Job(task_type=TaskType.CHECK_FULL, progress_total=1)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job.id, project


def test_do_move_finding_rejects_missing_semicolon_without_losing_state(db, tmp_path):
    with get_session() as session:
        job_id, _ = _make_finding_job(session, str(tmp_path))

    update, context, reply = _message_update(
        "a.py::foo no semicolon here", {"awaiting": "later_reason", "registry_job_id": job_id}
    )

    _run(check_module.on_text(update, context))

    reply.assert_awaited_once()
    assert "Формат" in reply.await_args.args[0]
    # awaiting/registry_job_id сохранены — пользователь может прислать
    # исправленный текст ещё раз, и он будет обработан.
    assert context.user_data["awaiting"] == "later_reason"
    assert context.user_data["registry_job_id"] == job_id


def test_do_move_finding_rejects_empty_reason_without_losing_state(db, tmp_path):
    with get_session() as session:
        job_id, _ = _make_finding_job(session, str(tmp_path))

    update, context, reply = _message_update(
        "a.py::foo;   ", {"awaiting": "later_reason", "registry_job_id": job_id}
    )

    _run(check_module.on_text(update, context))

    reply.assert_awaited_once()
    assert "Формат" in reply.await_args.args[0]
    assert context.user_data["awaiting"] == "later_reason"
    assert context.user_data["registry_job_id"] == job_id


def test_do_move_finding_retries_successfully_after_format_error(db, monkeypatch, tmp_path):
    """Ключевой сценарий бага: первая попытка без ';' проваливается,
    вторая (исправленная) попытка должна реально сработать — раньше
    состояние уже было бы обнулено после первой попытки."""
    with get_session() as session:
        job_id, _project = _make_finding_job(session, str(tmp_path))

    update, context, reply = _message_update(
        "a.py::foo no semicolon", {"awaiting": "later_reason", "registry_job_id": job_id}
    )
    _run(check_module.on_text(update, context))
    reply.assert_awaited_once()

    monkeypatch.setattr(check_module, "move_finding", lambda path, file_symbol, *, to, reason: True)
    update2, context2, reply2 = _message_update("a.py::foo; больше не актуально", context.user_data)

    _run(check_module.on_text(update2, context2))

    reply2.assert_awaited_once()
    assert "Перенесено" in reply2.await_args.args[0]
    assert context2.user_data["awaiting"] is None
    assert "registry_job_id" not in context2.user_data
