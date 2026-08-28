from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.db.models import Job, JobStatus
from app.db.session import get_session
from app.tasks import clarify


async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    with get_session() as session:
        job = session.scalar(
            select(Job).where(Job.created_by_tg_id == chat_id, Job.status == JobStatus.PAUSED_QUESTION)
        )
        job_id = job.id if job else None

    if job_id is None:
        return

    text = update.message.text.strip()
    if not text:
        return
    if clarify.answer(job_id, text):
        await update.message.reply_text("✅ Принято, продолжаю…")


def register(application: Application) -> None:
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer), group=8)
