"""/start — онбординг и главное меню."""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.bot.access_control import is_admin as check_is_admin
from app.bot.handlers.ai_chat import reset_stale_chat
from app.bot.keyboards import main_menu
from app.db.models import User
from app.db.session import get_session

ONBOARDING_TEXT = (
    "👋 Онбординг\n\n"
    "GitHub-токен и ключи ИИ-провайдеров задаются один раз на сервере бота "
    "(.env), не в чате — см. ⚙️ Настройки, там же указан статус подключения "
    "провайдеров ИИ.\n\n"
    "создано [yabl04K0](https://guns.lol/yabl04K0)"
)

MAIN_MENU_TEXT = (
    "🤖 ai-check-bot\n"
    "Оркестрация ИИ-провайдеров по вашим проектам\n"
    "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    "▸ ЧЕК/Фича/Фикс/Рефакторинг/Кастом — запуск задач\n"
    "▸ 🤖 Активность — что происходит прямо сейчас\n"
    "▸ ⚙️ Настройки — провайдеры, тиры, лимиты, права агентов"
)


def _ensure_user(tg_id: int, display_name: str | None, is_admin_default: bool) -> bool:
    """Возвращает True, если пользователь только что создан (первый /start)."""
    with get_session() as session:
        from sqlalchemy import select

        existing = session.scalar(select(User).where(User.tg_id == tg_id))
        if existing is not None:
            return False
        session.add(User(tg_id=tg_id, display_name=display_name, is_admin=is_admin_default))
        return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    admin = check_is_admin(update, context)

    is_new_user = _ensure_user(user.id, user.full_name, admin)

    if is_new_user:
        await update.effective_chat.send_message(
            ONBOARDING_TEXT, parse_mode="Markdown", disable_web_page_preview=True
        )

    # /start — естественный способ "сбросить и вернуться в меню", но сам
    # по себе не трогал context.user_data вообще: активный 🗨 ИИ-чат
    # оставался бы висеть в БД навсегда (см. reset_stale_chat).
    reset_stale_chat(context, user.id)
    await update.effective_chat.send_message(MAIN_MENU_TEXT, reply_markup=main_menu(is_admin=admin))


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
