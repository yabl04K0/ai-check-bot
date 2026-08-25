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

from app.bot.formatting import render_error, render_interrupted, render_progress, render_report_header
from app.bot.keyboards import approval_menu, progress_menu, report_menu
from app.db.models import HistoryEntry, Job, JobStatus, ProviderMode
from app.db.session import get_session
from app.logging_setup import log_action
from app.notifications.webhook import notify_external
from app.providers.ai_autonomy import job_needs_manual_approval
from app.providers.base import AIProvider, ProviderResult, RunOptions
from app.providers.registry import ProviderRegistry
from app.providers.router import NoProviderAvailableError, pick_provider
from app.providers.success_history import compute_success_scores
from app.registry_store.sync import sync_project_findings
from app.tasks.factory import build_pipeline
from app.tasks.pipeline import PipelineInterrupted, StepContext
from app.tasks.queue import JobQueue
from app.tasks.types import TASK_TYPE_LABELS

logger = logging.getLogger(__name__)

class _NoteTrackingProvider:
    """Прозрачная обёртка вокруг AIProvider для одной job:
    1. После каждого run_prompt сохраняет короткий фрагмент ответа в
       Job.progress_detail — чтобы прогресс в Telegram показывал не
       только номер шага, но и что ИИ реально только что сказал/сделал.
    2. Логирует ПОЛНЫЙ промпт и ПОЛНЫЙ ответ (не обрезанные до 400
       символов, как progress_detail) через стандартный logging — в тот
       же файл, что уже читается для диагностики (см. запрос пользователя
       "мало инфы, сделай логирование всех ответов"). Ошибки уже логируются
       выше по стеку (start_job's logger.exception с полным traceback) —
       тут не дублируем, только успешные вызовы.

    Пишет через свою короткую сессию (get_session()), не через
    ctx.session — Fleet-checkers/критики зовут run_prompt из нескольких
    потоков параллельно (ThreadPoolExecutor, см. app.tasks.protocol_full),
    а ctx.session на всех один и не потокобезопасна; тот же приём, что у
    app.providers.quota.QuotaTracker.record()."""

    def __init__(self, inner: AIProvider, job_id: int) -> None:
        self._inner = inner
        self._job_id = job_id

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        logger.info(
            "Job #%s [%s] ПРОМПТ (%d симв.):\n%s",
            self._job_id,
            self._inner.name.value,
            len(prompt),
            prompt[:3000] + ("…[обрезано]" if len(prompt) > 3000 else ""),
        )
        result = self._inner.run_prompt(prompt, options)
        logger.info(
            "Job #%s [%s] ОТВЕТ (%d симв., %d+%d ток.):\n%s",
            self._job_id,
            self._inner.name.value,
            len(result.text),
            result.input_tokens,
            result.output_tokens,
            result.text[:6000] + ("…[обрезано]" if len(result.text) > 6000 else ""),
        )
        snippet = " ".join(result.text.split())[:400]
        if snippet:
            with get_session() as session:
                job = session.get(Job, self._job_id)
                if job is not None:
                    job.progress_detail = snippet
        return result


CANCEL_REQUESTS: set[int] = set()  # job_id-ки, отменённые пользователем
PAUSE_REQUESTS: set[int] = set()  # job_id-ки, поставленные на ⏸ Паузу
# job_id-ки, для которых человек уже тапнул "✅ Разрешить" на экране
# подтверждения запуска (см. _request_start_approval) — без этого набора
# start_job() зациклился бы, повторно требуя подтверждение самого себя.
APPROVED_JOB_IDS: set[int] = set()

APPROVAL_REQUEST_TEXT = (
    "🔑 Задача #{job_id} ({label}) готова к запуску.\n\n"
    "Включён доступ ИИ к GITHUB_TOKEN (⚙️ Настройки → Автономность ИИ) — "
    "прежде чем ИИ-провайдер начнёт работу, подтверди запуск вручную, как "
    "в приложениях для вайб-кодинга. Роутер решает, какой провайдер "
    "возьмёт задачу, только в момент старта — заранее не предсказать,\n"
    "поэтому подтверждение спрашивается для любой задачи, пока доступ к "
    "токену включён, не только для тех, что достанутся Cursor.\n\n"
    "Отключить это подтверждение: ⚙️ Настройки → Автоодобрение команд."
)


async def start_job(application: Application, job_id: int) -> None:
    """Точка входа: берёт job, гоняет пайплайн, шлёт отчёт, берёт следующую из очереди.

    Единственный настоящий "старт выполнения" во всей кодовой базе (сюда
    сходятся confirm() в check.py, _enqueue_fix, scheduler.autocheck._tick
    и собственный хвост этой же функции, который берёт следующую задачу
    из очереди) — поэтому именно тут, а не в каждом месте вызова, стоит
    проверка на подтверждение запуска (см. app.providers.ai_autonomy)."""
    if job_needs_manual_approval() and job_id not in APPROVED_JOB_IDS:
        await _request_start_approval(application, job_id)
        return
    APPROVED_JOB_IDS.discard(job_id)

    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.error("start_job: job #%s не найден", job_id)
            return
        if JobQueue(session).is_busy():
            # Другая задача уже RUNNING/PAUSED_MANUAL — оставляем эту в
            # QUEUED, естественный дренаж (хвост этой же функции после
            # завершения текущей) подхватит её сам. Без этой проверки
            # гейт подтверждения (см. ai_autonomy.job_needs_manual_approval)
            # мог держать задачу QUEUED-но-не-busy минутами, пока человек
            # не тапнет "Разрешить" — за это время is_busy() у ДРУГОЙ,
            # параллельно запускаемой задачи (confirm()/_enqueue_fix/
            # autocheck._tick) тоже видит False и тоже пытается стартовать,
            # а mark_running() ниже по коду это никак не проверял.
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


async def _request_start_approval(application: Application, job_id: int) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.error("_request_start_approval: job #%s не найден", job_id)
            return
        chat_id = job.created_by_tg_id
        task_type = job.task_type

    if chat_id is None:
        # Некому подтверждать (задача без chat_id, например будущий
        # неинтерактивный источник) — не блокируем очередь навечно, но и
        # не выполняем без подтверждения молча: явная ошибка вместо
        # тихого зависания в QUEUED.
        with get_session() as session:
            job = session.get(Job, job_id)
            JobQueue(session).mark_error(
                job,
                "Требуется подтверждение запуска (включён доступ ИИ к GITHUB_TOKEN), "
                "но у задачи нет chat_id — некому его показать.",
            )
        return

    label = TASK_TYPE_LABELS.get(task_type, task_type.value if hasattr(task_type, "value") else task_type)
    text = APPROVAL_REQUEST_TEXT.format(job_id=job_id, label=label)
    try:
        await application.bot.send_message(chat_id, text, reply_markup=approval_menu(job_id))
    except TelegramError:
        logger.exception("Не удалось отправить запрос на подтверждение запуска по job #%s", job_id)


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
            success_scores = compute_success_scores(session, job.task_type)
            try:
                provider_name = pick_provider(job.task_type, registry, success_scores=success_scores)
            except NoProviderAvailableError as exc:
                queue.mark_error(job, str(exc))
                session.commit()
                return {}
            job.provider = provider_name
            job.provider_mode = ProviderMode.AUTO
            session.commit()

        provider = _NoteTrackingProvider(registry.get(job.provider), job.id)
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
            paused_requested=lambda: job_id in PAUSE_REQUESTS,
        )
        try:
            pipeline.run(ctx, queue)
        except PipelineInterrupted:
            pass  # уже записано в job (paused_quota/cancelled) движком
        except Exception:  # noqa: BLE001 — ошибка уже записана queue.mark_error
            logger.exception("Job #%s упал с необработанной ошибкой", job_id)
        finally:
            CANCEL_REQUESTS.discard(job_id)
            PAUSE_REQUESTS.discard(job_id)

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


_ACTIVE_STATUSES = (JobStatus.RUNNING, JobStatus.PAUSED_MANUAL)


async def _progress_loop(application, job_id: int, chat_id: int | None, message) -> None:
    if chat_id is None or message is None:
        return
    last_text = None
    last_paused = None
    while True:
        await asyncio.sleep(3)
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is None or job.status not in _ACTIVE_STATUSES:
                return
            paused = job.status == JobStatus.PAUSED_MANUAL
            text = render_progress(job)
        if text != last_text or paused != last_paused:
            try:
                await message.edit_text(text, reply_markup=progress_menu(job_id, paused=paused))
                last_text, last_paused = text, paused
            except TelegramError:
                pass


async def _deliver_outcome(
    application, job_id: int, chat_id: int, status: JobStatus, progress_message
) -> None:
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

    # Дублирование в Slack/Discord — best-effort, никогда не должно
    # ломать доставку в Telegram (см. notify_external), поэтому вызывается
    # уже после неё, независимо от того, удалась она или нет.
    settings = getattr(application, "bot_data", {}).get("settings")
    notifications = getattr(settings, "notifications", None)
    if notifications and (notifications.slack_webhook_url or notifications.discord_webhook_url):
        await notify_external(
            text,
            slack_webhook_url=notifications.slack_webhook_url,
            discord_webhook_url=notifications.discord_webhook_url,
        )
