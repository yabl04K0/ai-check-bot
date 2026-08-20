"""Minimal Telegram entrypoint: admin-only text commands to manage AI accounts and their
probe schedules. Deliberately not an inline-keyboard menu yet — the UX pass over the
sibling bots' menu patterns (PROJECT_MEMORY.md backlog) has not happened, and building a
polished menu before that would likely be thrown away."""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from ai_check_bot.config import get_settings
from ai_check_bot.db import make_session_factory
from ai_check_bot.models import AIAccount
from ai_check_bot.probe_service import (
    InvalidTimeError,
    ScheduleLimitError,
    add_account,
    add_schedule,
    run_probe,
)
from ai_check_bot.scheduler import build_scheduler, sync_jobs

logger = logging.getLogger(__name__)


def _is_admin(update: Update, admin_tg_id: int | None) -> bool:
    return admin_tg_id is not None and update.effective_user is not None and update.effective_user.id == admin_tg_id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ai-check-bot. Команды (только админ, в личке):\n"
        "/add_account <label> <provider> <api_key> [proxy_url]\n"
        "/add_schedule <label> <HH:MM> [сообщение]\n"
        "/accounts\n"
        "/probe_now <label>"
    )


async def add_account_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _is_admin(update, settings.admin_tg_id):
        return
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text("формат: /add_account <label> <provider> <api_key> [proxy_url]")
        return
    label, provider, api_key = args[0], args[1], args[2]
    proxy_url = args[3] if len(args) > 3 else None
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        try:
            add_account(session, provider=provider, label=label, api_key=api_key, proxy_url=proxy_url)
        except IntegrityError:
            session.rollback()
            await update.message.reply_text(f"не добавлено: label '{label}' уже занят")
            return
    await update.message.reply_text(f"аккаунт '{label}' ({provider}) добавлен")


async def add_schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _is_admin(update, settings.admin_tg_id):
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("формат: /add_schedule <label> <HH:MM> [сообщение]")
        return
    label, time_of_day = args[0], args[1]
    message = " ".join(args[2:]) or "ping"
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.query(AIAccount).filter_by(label=label).one_or_none()
        if account is None:
            await update.message.reply_text(f"нет аккаунта '{label}'")
            return
        try:
            add_schedule(session, account=account, time_of_day=time_of_day, message=message)
        except (InvalidTimeError, ScheduleLimitError) as exc:
            await update.message.reply_text(f"не добавлено: {exc}")
            return
        except IntegrityError:
            session.rollback()
            await update.message.reply_text(f"не добавлено: у '{label}' уже есть пробник на {time_of_day}")
            return
    sync_jobs(context.bot_data["scheduler"], session_factory)
    await update.message.reply_text(f"расписание для '{label}' на {time_of_day} (UTC) добавлено")


async def accounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _is_admin(update, settings.admin_tg_id):
        return
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        accounts = session.query(AIAccount).all()
        if not accounts:
            await update.message.reply_text("аккаунтов нет")
            return
        lines = []
        for acc in accounts:
            times = ", ".join(s.time_of_day for s in acc.schedules if s.enabled) or "—"
            lines.append(f"{acc.label} [{acc.provider}] расписание: {times}")
        await update.message.reply_text("\n".join(lines))


async def probe_now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not _is_admin(update, settings.admin_tg_id):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("формат: /probe_now <label>")
        return
    label = args[0]
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.query(AIAccount).filter_by(label=label).one_or_none()
        if account is None:
            await update.message.reply_text(f"нет аккаунта '{label}'")
            return
        run = await run_probe(session, account, "ping")
    if run.success:
        await update.message.reply_text(f"ok, {run.latency_ms} мс")
    else:
        await update.message.reply_text(f"ошибка: {run.error}")


def build_application() -> Application:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    session_factory = make_session_factory(settings.db_path)
    scheduler = build_scheduler(session_factory)

    application = Application.builder().token(settings.bot_token).build()
    application.bot_data["session_factory"] = session_factory
    application.bot_data["scheduler"] = scheduler

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_account", add_account_cmd))
    application.add_handler(CommandHandler("add_schedule", add_schedule_cmd))
    application.add_handler(CommandHandler("accounts", accounts_cmd))
    application.add_handler(CommandHandler("probe_now", probe_now_cmd))

    scheduler.start()
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
