"""Движок пайплайна: упорядоченные шаги, прогресс, HANDOVER на обрыв квоты.

Пайплайны (protocol_full/protocol_lite/generic) собирают список Step и
отдают их сюда. Сам движок не знает деталей ЧЕКа/фичи — только умеет
идти по шагам, обновлять прогресс в Job и корректно останавливаться.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus, Project
from app.providers.base import AIProvider, ProviderQuotaExceededError
from app.tasks.queue import JobQueue

ProgressCallback = Callable[[int, str], None]
PAUSE_POLL_SECONDS = 2


class PipelineInterrupted(Exception):
    """Пайплайн остановлен на середине (квота, отмена) — не ошибка выполнения."""


class PipelineCancelled(PipelineInterrupted):
    pass


@dataclass
class StepContext:
    job: Job
    projects: list[Project]
    provider: AIProvider
    session: Session
    comment: str | None = None
    scope: str | None = None
    cancel_requested: Callable[[], bool] = lambda: False
    paused_requested: Callable[[], bool] = lambda: False
    # свободное состояние, шаги читают/пишут в него результаты друг друга
    state: dict = field(default_factory=dict)


class Step(ABC):
    """Один шаг протокола. Обязан быть безопасным для повторного вызова
    ТОЛЬКО если сам это гарантирует — движок не даёт такой гарантии сам по
    себе (editing-роли Cursor, например, не идемпотентны — см. cursor.py)."""

    label: str

    @abstractmethod
    def run(self, ctx: StepContext) -> None:
        """Выполняет шаг, кладёт результат в ctx.state. Может кинуть
        ProviderQuotaExceededError — движок поймает и уйдёт в HANDOVER."""


class Pipeline:
    def __init__(self, steps: list[Step]) -> None:
        self._steps = steps

    @property
    def total_steps(self) -> int:
        return len(self._steps)

    def run(self, ctx: StepContext, queue: JobQueue) -> StepContext:
        # progress_total выставленный при enqueue — оценка "на глаз" (см.
        # tasks.types.STEP_COUNT); реальное число шагов известно только
        # когда пайплайн собран, поэтому синкаем его здесь перед стартом.
        # Коммитим на каждом переходе — job.progress_* читает отдельный
        # процесс/поток (Telegram-прогресс-бар) через свою короткую сессию,
        # ему нужны видимые на диске изменения, а не только в памяти.
        ctx.job.progress_total = self.total_steps
        ctx.session.commit()

        for index, step in enumerate(self._steps, start=1):
            self._wait_while_paused(ctx, queue)

            if ctx.cancel_requested():
                queue.mark_cancelled(ctx.job)
                ctx.session.commit()
                raise PipelineCancelled(f"Отменено пользователем на шаге {index}/{self.total_steps}")

            queue.update_progress(ctx.job, index - 1, step.label)
            ctx.session.commit()
            try:
                step.run(ctx)
            except ProviderQuotaExceededError as exc:
                handover = (
                    f"Обрыв на шаге {index}/{self.total_steps} ({step.label}): {exc}\n"
                    f"Сделано: шаги 1..{index - 1}. Открыто: текущий шаг '{step.label}'.\n"
                    f"Дальше: возобновить с шага {index} после сброса квоты провайдера."
                )
                queue.mark_paused_quota(ctx.job, handover)
                ctx.session.commit()
                raise PipelineInterrupted(handover) from exc
            except Exception as exc:  # noqa: BLE001 — любая другая ошибка шага
                queue.mark_error(ctx.job, f"Ошибка на шаге {index}/{self.total_steps} ({step.label}): {exc}")
                ctx.session.commit()
                raise

            queue.update_progress(ctx.job, index, step.label)
            ctx.session.commit()

        ctx.job.report_text = ctx.state.get("final_report")
        ctx.job.patch_text = ctx.state.get("patch")
        queue.mark_done(ctx.job)
        ctx.session.commit()
        return ctx

    @staticmethod
    def _wait_while_paused(ctx: StepContext, queue: JobQueue) -> None:
        """Блокирует поток (не event loop — это выполняется внутри
        asyncio.to_thread, см. app.bot.job_runner) пока job на ⏸ Паузе.
        Проверяется между шагами, не посреди одного — шаг (обычно один
        LLM-вызов) не прерывается на середине."""
        if not ctx.paused_requested():
            return

        queue.mark_paused_manual(ctx.job)
        ctx.session.commit()
        try:
            while ctx.paused_requested():
                if ctx.cancel_requested():
                    return  # отмену обработает вызывающий цикл сразу после
                time.sleep(PAUSE_POLL_SECONDS)
        finally:
            if ctx.job.status == JobStatus.PAUSED_MANUAL and not ctx.cancel_requested():
                queue.mark_resumed(ctx.job)
                ctx.session.commit()
