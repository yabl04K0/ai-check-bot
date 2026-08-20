"""UI helpers — pattern ported from the sibling bots (MeCelium's aiogram ui.py, adapted
to python-telegram-bot): one editable message per screen, OK to dismiss a final result,
🔙 to go back. See PROJECT_MEMORY.md for which sibling this pattern came from."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CB_OK = "ui:ok"


def with_back(rows: list[list[InlineKeyboardButton]], back_to: str) -> InlineKeyboardMarkup:
    """Append a 🔙 Назад row under an existing keyboard, navigating to `back_to`."""
    return InlineKeyboardMarkup([*rows, [InlineKeyboardButton("🔙 Назад", callback_data=back_to)]])


def ok_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("OK", callback_data=CB_OK)]


async def edit_or_send(update: Update, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the callback's message in place; fall back to a new message for a plain
    command. Swallows 'message is not modified' (harmless no-op), re-raises anything else
    after logging — a silent failure here means the user stares at a stale screen."""
    if update.callback_query is not None:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.warning("edit_message_text failed (%s), sending new message", exc)
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
        return
    await update.message.reply_text(text, reply_markup=reply_markup)


async def dismiss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except BadRequest:
        pass
