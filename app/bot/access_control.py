"""Единая точка авторизации.

Бот выполняет git commit, трогает видимость GitHub-репо и тратит платную
квоту ИИ-провайдеров — это не публичный сервис, а личный инструмент (см.
README). Раньше это разделение было только у Админки (👑) — сам ЧЕК,
Проекты, GitHub-модуль, Провайдеры ИИ были доступны ЛЮБОМУ, кто напишет
боту в Telegram. Этот модуль закрывает это одним общим гейтом вместо
разбросанных проверок по каждому хендлеру.

Регистрируется в группе с самым низким номером (см. app/bot/app.py) —
PTB проходит группы по возрастанию, ApplicationHandlerStop прерывает
обработку текущего update для ВСЕХ следующих групп сразу.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

DENIED_TEXT = "🔒 Этот бот приватный — доступ только у владельца."

GATE_GROUP = -100  # ниже (раньше) любой другой группы хендлеров в проекте

_warned_unconfigured = False


def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """В отличие от is_authorized (пускает ли бот вообще этого юзера),
    это "тот самый единственный владелец из ADMIN_TG_ID" — при
    незаданном ADMIN_TG_ID (открытый режим, см. is_authorized ниже)
    возвращает False для всех, а не True, т.к. открывать 👑 Админку
    всем подряд в этом режиме не нужно."""
    settings = context.application.bot_data["settings"]
    user = update.effective_user
    return bool(settings.admin_tg_id and user and user.id == settings.admin_tg_id)


def is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = context.application.bot_data["settings"]
    if not settings.admin_tg_id:
        global _warned_unconfigured
        if not _warned_unconfigured:
            logger.warning(
                "ADMIN_TG_ID не задан — бот открыт ЛЮБОМУ пользователю Telegram. "
                "Если это не намеренно, задай ADMIN_TG_ID в .env."
            )
            _warned_unconfigured = True
        return True
    user = update.effective_user
    return bool(user and user.id == settings.admin_tg_id)


async def _gate_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_authorized(update, context):
        return
    if update.callback_query:
        await update.callback_query.answer(DENIED_TEXT, show_alert=True)
    logger.info("Отказано в доступе (callback): tg_id=%s", update.effective_user and update.effective_user.id)
    raise ApplicationHandlerStop


async def _gate_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_authorized(update, context):
        return
    if update.effective_message:
        await update.effective_message.reply_text(DENIED_TEXT)
    logger.info("Отказано в доступе (message): tg_id=%s", update.effective_user and update.effective_user.id)
    raise ApplicationHandlerStop


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(_gate_callback_query), group=GATE_GROUP)
    application.add_handler(MessageHandler(filters.ALL, _gate_message), group=GATE_GROUP)
