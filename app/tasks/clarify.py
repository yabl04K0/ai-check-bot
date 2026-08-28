from __future__ import annotations

import asyncio
import logging
import time

from app.db.models import Job, JobStatus
from app.db.session import get_session

logger = logging.getLogger(__name__)

_PENDING: dict[int, str | None] = {}

POLL_SECONDS = 2
DEFAULT_TIMEOUT_SECONDS = 1800


def answer(job_id: int, text: str) -> bool:
    if job_id not in _PENDING:
        return False
    _PENDING[job_id] = text
    return True


def has_pending(job_id: int) -> bool:
    return job_id in _PENDING


def ask_and_wait(
    application,
    job_id: int,
    chat_id: int | None,
    question: str,
    *,
    timeout: int | None = None,
    cancel_requested=lambda: False,
) -> str | None:
    if chat_id is None:
        return None
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SECONDS

    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        job.pending_question = question
        job.status = JobStatus.PAUSED_QUESTION

    text = f"❓ ИИ спрашивает по задаче #{job_id}:\n\n{question}\n\nОтветь обычным сообщением."
    try:
        asyncio.run(application.bot.send_message(chat_id, text))
    except Exception:  # noqa: BLE001
        logger.exception("clarify.ask_and_wait: не удалось отправить вопрос по job #%s", job_id)
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.pending_question = None
                if job.status == JobStatus.PAUSED_QUESTION:
                    job.status = JobStatus.RUNNING
        return None

    _PENDING[job_id] = None
    waited = 0
    try:
        while _PENDING.get(job_id) is None:
            if waited >= timeout or cancel_requested():
                return None
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        return _PENDING.get(job_id)
    finally:
        _PENDING.pop(job_id, None)
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.pending_question = None
                if job.status == JobStatus.PAUSED_QUESTION:
                    job.status = JobStatus.RUNNING
