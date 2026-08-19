"""Главное меню + общие мелочи (dismiss-кнопка на уведомлениях)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.bot.handlers.start import MAIN_MENU_TEXT
from app.bot.keyboards import main_menu


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("flow", None)
    context.user_data.pop("awaiting", None)
    await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=main_menu())


async def dismiss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — сообщение могло уже устареть
        pass


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(dismiss, pattern=r"^dismiss$"))
