"""DM владельцу с кнопкой OK (см. app.bot.keyboards.dismiss_menu) —
"кучи уведов" из запроса пользователя про пул прокси: прокси заменён,
пул исчерпан, не хватает прокси на все аккаунты/API. Тот же паттерн, что
уже используют финальные сообщения бота — кнопка снимает клавиатуру, не
требует отдельного ответа."""

from __future__ import annotations

import logging

from telegram.error import TelegramError
from telegram.ext import Application

from app.bot.keyboards import dismiss_menu

logger = logging.getLogger(__name__)


async def notify_admin(application: Application, text: str) -> None:
    settings = application.bot_data["settings"]
    if not settings.admin_tg_id:
        return
    try:
        await application.bot.send_message(settings.admin_tg_id, text, reply_markup=dismiss_menu())
    except TelegramError:
        logger.exception("Не удалось отправить уведомление о прокси владельцу")
