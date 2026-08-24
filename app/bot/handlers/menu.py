"""Главное меню + общие мелочи (dismiss-кнопка на уведомлениях)."""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.bot.handlers.start import MAIN_MENU_TEXT
from app.bot.keyboards import back_button, main_menu
from app.providers.quota import account_usage_summary


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("flow", None)
    context.user_data.pop("awaiting", None)
    await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=main_menu())


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def limits_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Самооценка расхода токенов за 5ч/неделю по каждому провайдеру и
    аккаунту — см. app.providers.quota.account_usage_summary. Не %, не
    официальные данные Anthropic (такого API нет ни у подписки Claude
    Code, ни у большинства остальных провайдеров) — только то, что бот
    сам отправил, честно посчитанное."""
    registry = context.application.bot_data["provider_registry"]
    lines = ["📊 Лимиты (самооценка бота — не % от Anthropic, официального API нет)", ""]
    any_usage = False
    for name in registry.all():
        summary = account_usage_summary(name)
        if not summary:
            continue
        any_usage = True
        lines.append(f"{name.value}:")
        for label, (five_h, week) in sorted(summary.items(), key=lambda kv: kv[0] or ""):
            label_text = label or "(без метки)"
            lines.append(f"  {label_text} — 5ч: {_fmt_tokens(five_h)} · неделя: {_fmt_tokens(week)}")
    if not any_usage:
        lines.append("Пока пусто — ни одного вызова ИИ ещё не залогировано.")
    return "\n".join(lines)


async def show_limits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(limits_text(context), reply_markup=back_button())


async def dismiss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — сообщение могло уже устареть
        pass


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(show_limits, pattern=r"^menu:limits$"))
    application.add_handler(CallbackQueryHandler(dismiss, pattern=r"^dismiss$"))
