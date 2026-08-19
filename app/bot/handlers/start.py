"""/start — онбординг и главное меню."""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.db.models import User
from app.db.session import get_session
from app.bot.keyboards import main_menu

ONBOARDING_TEXT = (
    "👋 Онбординг\n\n"
    "GitHub-токен и ключи ИИ-провайдеров задаются один раз на сервере бота "
    "(.env), не в чате — см. Настройки → 🔌 Провайдеры ИИ для статуса "
    "подключения."
)

MAIN_MENU_TEXT = "🏠 ГЛАВНОЕ МЕНЮ"


def _ensure_user(tg_id: int, display_name: str | None, is_admin_default: bool) -> None:
    with get_session() as session:
        from sqlalchemy import select

        existing = session.scalar(select(User).where(User.tg_id == tg_id))
        if existing is None:
            session.add(User(tg_id=tg_id, display_name=display_name, is_admin=is_admin_default))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    settings = context.application.bot_data["settings"]
    is_admin = bool(settings.admin_tg_id and user.id == settings.admin_tg_id)
    first_time = context.application.bot_data.get("known_users", set())
    seen_before = user.id in first_time
    first_time.add(user.id)
    context.application.bot_data["known_users"] = first_time

    _ensure_user(user.id, user.full_name, is_admin)

    if not seen_before:
        await update.effective_chat.send_message(ONBOARDING_TEXT)

    await update.effective_chat.send_message(MAIN_MENU_TEXT, reply_markup=main_menu())


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
