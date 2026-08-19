"""Исполнение Job: запуск пайплайна в фоне, живой прогресс-бар в чате,
доставка отчёта, продвижение очереди дальше.

Упрощение для v0: created_by_tg_id используется и как chat_id получателя
отчёта (бот — личный инструмент, юзер общается с ним в приватном чате,
где chat.id == user.id). Если бот когда-нибудь станет групповым, это
надо развести на отдельное поле.
"""

from __future__ import annotations

import asyncio
import logging

from telegram.error import TelegramError
from telegram.ext import Application

from app.db.models import HistoryEntry, Job, JobStatus, ProviderMode
from app.db.session import get_session
from app.bot.formatting import render_error, render_interrupted, render_progress, render_report_header
from app.bot.keyboards import progress_menu, report_menu
from app.logging_setup import log_action
from app.providers.registry import ProviderRegistry
from app.providers.router import NoProviderAvailableError, pick_provider
from app.registry_store.sync import sync_project_findings
from app.tasks.factory import build_pipeline
from app.tasks.pipeline import PipelineInterrupted, StepContext
from app.tasks.queue import JobQueue
from app.tasks.types import TASK_TYPE_LABELS

logger = logging.getLogger(__name__)

CANCEL_REQUESTS: set[int] = set()  # job_id-ки, отменённые пользователем


async def start_job(application: Application, job_id: int) -> None:
    """Точка входа: берёт job, гоняет пайплайн, шлёт отчёт, берёт следующую из очереди."""
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.error("start_job: job #%s не найден", job_id)
            return
        chat_id = job.created_by_tg_id
        task_type = job.task_type

    log_action(str(chat_id or "system"), "job_started", f"#{job_id} {task_type.value}")

    progress_message = None
    if chat_id:
        try:
            progress_message = await application.bot.send_message(
                chat_id,
                f"▶️ Запускаю {TASK_TYPE_LABELS.get(task_type, task_type)}…",
                reply_markup=progress_menu(job_id),
            )
        except TelegramError:
            logger.exception("Не удалось отправить стартовое сообщение по job #%s", job_id)

    progress_task = asyncio.create_task(_progress_loop(application, job_id, chat_id, progress_message))

    try:
        await asyncio.to_thread(_run_pipeline_blocking, application, job_id)
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

    with get_session() as session:
        job = session.get(Job, job_id)
        status = job.status

    log_action(str(chat_id or "system"), "job_finished", f"#{job_id} status={status.value}")

    if chat_id:
        await _deliver_outcome(application, job_id, chat_id, status, progress_message)

    # Продвигаем очередь — берём следующую задачу, если освободились.
    with get_session() as session:
        queue = JobQueue(session)
        next_job = queue.next_queued()
        next_job_id = next_job.id if next_job else None
    if next_job_id:
        asyncio.create_task(start_job(application, next_job_id))


def _run_pipeline_blocking(application: Application, job_id: int) -> dict:
    """Выполняется в отдельном потоке (asyncio.to_thread) — вся синхронная
    работа с БД/провайдерами/subprocess живёт здесь, не в event loop."""
    registry: ProviderRegistry = application.bot_data["provider_registry"]

    with get_session() as session:
        job = session.get(Job, job_id)
        queue = JobQueue(session)
        queue.mark_running(job)
        session.commit()

        if job.provider is None:
            try:
                provider_name = pick_provider(job.task_type, registry)
            except NoProviderAvailableError as exc:
                queue.mark_error(job, str(exc))
                session.commit()
                return {}
            job.provider = provider_name
            job.provider_mode = ProviderMode.AUTO
            session.commit()

        provider = registry.get(job.provider)
        projects = list(job.projects)
        pipeline = build_pipeline(job.task_type)
        ctx = StepContext(
            job=job,
            projects=projects,
            provider=provider,
            session=session,
            comment=job.comment,
            scope=job.scope,
            cancel_requested=lambda: job_id in CANCEL_REQUESTS,
        )
        try:
            pipeline.run(ctx, queue)
        except PipelineInterrupted:
            pass  # уже записано в job (paused_quota/cancelled) движком
        except Exception:  # noqa: BLE001 — ошибка уже записана queue.mark_error
            logger.exception("Job #%s упал с необработанной ошибкой", job_id)
        finally:
            CANCEL_REQUESTS.discard(job_id)

        if job.status == JobStatus.DONE:
            for project in projects:
                session.add(
                    HistoryEntry(
                        project_id=project.id,
                        job_id=job.id,
                        task_type=job.task_type,
                        provider=job.provider,
                        provider_mode=job.provider_mode,
                        result_summary=(job.report_text or "")[:2000],
                    )
                )
                # Job мог поменять содержимое chek_*.md проекта (Full ЧЕК
                # регистрирует находки, см. app/tasks/protocol_full.py) —
                # приводим SQLite-кэш в соответствие перед тем, как отдать
                # управление боту.
                sync_project_findings(session, project)
            session.commit()

        return dict(ctx.state)


async def _progress_loop(application, job_id: int, chat_id: int | None, message) -> None:
    if chat_id is None or message is None:
        return
    last_text = None
    while True:
        await asyncio.sleep(3)
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is None or job.status != JobStatus.RUNNING:
                return
            text = render_progress(job)
        if text != last_text:
            try:
                await message.edit_text(text, reply_markup=progress_menu(job_id))
                last_text = text
            except TelegramError:
                pass


async def _deliver_outcome(application, job_id: int, chat_id: int, status: JobStatus, progress_message) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        is_check = job.task_type.value.startswith("check")

        if status == JobStatus.DONE:
            summary = render_report_header(job)
            report_text = job.report_text or "Готово."
            text = f"{summary}\n\n{report_text[:3500]}"
            markup = report_menu(job_id, is_check=is_check)
        elif status == JobStatus.PAUSED_QUOTA:
            text = render_interrupted(job)
            markup = None
        elif status == JobStatus.CANCELLED:
            text = "✖ Отменено."
            markup = None
        else:
            text = render_error(job)
            markup = None

    try:
        await application.bot.send_message(chat_id, text[:4000], reply_markup=markup)
    except TelegramError:
        logger.exception("Не удалось доставить отчёт по job #%s", job_id)
