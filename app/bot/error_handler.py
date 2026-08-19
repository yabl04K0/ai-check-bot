"""Глобальный обработчик ошибок PTB.

Без него любое необработанное исключение внутри хендлера (сетевой сбой,
неучтённый edge case, гонка с уже устаревшим сообщением) PTB просто
логирует в stderr и молчит — юзер видит нажатую кнопку, которая ничего
не сделала, без единого намёка, что что-то пошло не так."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.logging_setup import log_action

logger = logging.getLogger(__name__)

USER_FACING_ERROR = "❌ Что-то пошло не так. Попробуй ещё раз или вернись в /start."


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необработанная ошибка при обработке update: %s", update, exc_info=context.error)

    error_summary = f"{type(context.error).__name__}: {context.error}"
    log_action("system", "unhandled_error", error_summary[:2000])

    chat_id = None
    if isinstance(update, Update) and update.effective_chat:
        chat_id = update.effective_chat.id

    if chat_id is None:
        return
    try:
        await context.bot.send_message(chat_id, USER_FACING_ERROR)
    except TelegramError:
        logger.exception("Не удалось уведомить чат %s об ошибке", chat_id)
