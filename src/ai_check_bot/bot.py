"""Telegram entrypoint. Inline-keyboard menu (pattern from sd-forge-bot's keyboards.py —
see PROJECT_MEMORY.md), free-text input via input_flow's waiting_for/TTL, and the jobs.py
live-status engine demonstrated on "probe every account now"."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.exc import IntegrityError
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import ai_check_bot.keyboards as kb
from ai_check_bot import jobs
from ai_check_bot.config import get_settings
from ai_check_bot.db import make_session_factory
from ai_check_bot.input_flow import pop_waiting, set_waiting
from ai_check_bot.models import AIAccount, ProbeSchedule
from ai_check_bot.probe_service import (
    InvalidProxyError,
    InvalidTimeError,
    ScheduleLimitError,
    UnknownProviderError,
    add_account,
    add_schedule,
    delete_account,
    run_probe,
    set_account_enabled,
    set_account_proxy,
)
from ai_check_bot.providers.router import pick_account
from ai_check_bot.scheduler import build_scheduler, sync_jobs
from ai_check_bot.task_service import NoAccountAvailableError, run_custom_task
from ai_check_bot.ui import CB_OK, dismiss, edit_or_send

TELEGRAM_MAX_MESSAGE_LEN = 4096
DEFAULT_TASK_PROVIDER = "claude"  # only provider implemented so far — see providers/registry.py

logger = logging.getLogger(__name__)


def _is_admin(update: Update, admin_tg_id: int | None) -> bool:
    return admin_tg_id is not None and update.effective_user is not None and update.effective_user.id == admin_tg_id


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True and continue, or False after answering the callback so the client stops
    showing a spinner. Every handler below calls this first."""
    settings = get_settings()
    if _is_admin(update, settings.admin_tg_id):
        return True
    if update.callback_query is not None:
        await update.callback_query.answer("Только для администратора", show_alert=True)
    return False


def _schedule_counts(session, accounts: list[AIAccount]) -> dict[int, int]:
    return {
        acc.id: session.query(ProbeSchedule).filter_by(account_id=acc.id, enabled=True).count()
        for acc in accounts
    }


# ---------------------------------------------------------------------------
# top-level entry points
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await edit_or_send(update, "ai-check-bot", reply_markup=kb.main_menu())


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    await edit_or_send(update, "ai-check-bot", reply_markup=kb.main_menu())


async def dismiss_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await dismiss(update, context)


# ---------------------------------------------------------------------------
# accounts list + add
# ---------------------------------------------------------------------------


async def show_accounts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        accounts = session.query(AIAccount).all()
        counts = _schedule_counts(session, accounts)
        text = "Аккаунты ИИ-провайдеров:" if accounts else "Аккаунтов пока нет."
        await edit_or_send(update, text, reply_markup=kb.accounts_menu(accounts, counts))


async def prompt_add_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    set_waiting(context, "add_account")
    await edit_or_send(
        update,
        "Пришли одной строкой: label provider api_key [proxy_url]\n"
        "Например: work claude sk-ant-... или work claude sk-ant-... socks5://127.0.0.1:1080\n"
        "provider сейчас поддерживается только: claude",
    )


async def _handle_add_account_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    parts = text.split()
    if len(parts) < 3:
        await update.message.reply_text("формат: label provider api_key [proxy_url]")
        return
    label, provider, api_key = parts[0], parts[1], parts[2]
    proxy_url = parts[3] if len(parts) > 3 else None
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        try:
            add_account(session, provider=provider, label=label, api_key=api_key, proxy_url=proxy_url)
        except IntegrityError:
            session.rollback()
            await update.message.reply_text(f"не добавлено: label '{label}' уже занят")
            return
        except UnknownProviderError as exc:
            await update.message.reply_text(f"не добавлено: {exc}")
            return
    await update.message.reply_text(f"аккаунт '{label}' ({provider}) добавлен", reply_markup=kb.main_menu())


# ---------------------------------------------------------------------------
# account detail
# ---------------------------------------------------------------------------


def _account_id_from(callback_data: str, prefix: str) -> int:
    return int(callback_data[len(prefix):])


async def show_account_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "acc:")
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.get(AIAccount, account_id)
        if account is None:
            await edit_or_send(update, "аккаунт удалён", reply_markup=kb.main_menu())
            return
        n = session.query(ProbeSchedule).filter_by(account_id=account.id, enabled=True).count()
        status = "включён" if account.enabled else "выключен"
        await edit_or_send(
            update,
            f"{account.label} [{account.provider}] — {status}",
            reply_markup=kb.account_detail_menu(account, n),
        )


async def prompt_set_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "acc:proxy:")
    set_waiting(context, "set_proxy", account_id=str(account_id))
    await edit_or_send(
        update,
        "Пришли адрес прокси (socks5://host:port или http(s)://host:port), "
        "или слово 'нет' чтобы убрать прокси у аккаунта.",
    )


async def _handle_set_proxy_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, account_id: int) -> None:
    proxy_url = None if text.strip().lower() in ("нет", "no", "none", "-") else text.strip()
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.get(AIAccount, account_id)
        if account is None:
            await update.message.reply_text("аккаунт удалён")
            return
        try:
            set_account_proxy(session, account, proxy_url)
        except InvalidProxyError as exc:
            await update.message.reply_text(f"не сохранено: {exc}")
            return
    await update.message.reply_text("прокси обновлён", reply_markup=kb.main_menu())


async def probe_account_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer("Проверяю…")
    account_id = _account_id_from(update.callback_query.data, "acc:probe:")
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.get(AIAccount, account_id)
        if account is None:
            await edit_or_send(update, "аккаунт удалён", reply_markup=kb.main_menu())
            return
        run = await run_probe(session, account, "ping")
        n = session.query(ProbeSchedule).filter_by(account_id=account.id, enabled=True).count()
        result = f"ok, {run.latency_ms} мс" if run.success else f"ошибка: {run.error}"
        await edit_or_send(
            update,
            f"{account.label} [{account.provider}] — {result}",
            reply_markup=kb.account_detail_menu(account, n),
        )


async def toggle_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "acc:toggle:")
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.get(AIAccount, account_id)
        if account is None:
            await edit_or_send(update, "аккаунт удалён", reply_markup=kb.main_menu())
            return
        set_account_enabled(session, account, not account.enabled)
        n = session.query(ProbeSchedule).filter_by(account_id=account.id, enabled=True).count()
        status = "включён" if account.enabled else "выключен"
        await edit_or_send(update, f"{account.label} [{account.provider}] — {status}", reply_markup=kb.account_detail_menu(account, n))


async def confirm_delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "acc:delconfirm:")
    await edit_or_send(update, "Удалить аккаунт и всю его историю проверок?", reply_markup=kb.confirm_delete_menu(account_id))


async def do_delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "acc:delete:")
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.get(AIAccount, account_id)
        if account is not None:
            delete_account(session, account)
    sync_jobs(context.bot_data["scheduler"], session_factory)
    await show_accounts_menu(update, context)


# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------


async def show_schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "sch:")
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        schedules = session.query(ProbeSchedule).filter_by(account_id=account_id, enabled=True).all()
        await edit_or_send(update, "Расписание проверок (UTC):", reply_markup=kb.schedule_menu(account_id, schedules))


async def prompt_add_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    account_id = _account_id_from(update.callback_query.data, "sch:add:")
    set_waiting(context, "add_schedule", account_id=str(account_id))
    await edit_or_send(update, "Пришли время в UTC как HH:MM, можно добавить свой текст пробника через пробел.")


async def _handle_add_schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, account_id: int) -> None:
    parts = text.split(maxsplit=1)
    time_of_day = parts[0]
    message = parts[1] if len(parts) > 1 else "ping"
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        account = session.get(AIAccount, account_id)
        if account is None:
            await update.message.reply_text("аккаунт удалён")
            return
        try:
            add_schedule(session, account=account, time_of_day=time_of_day, message=message)
        except (InvalidTimeError, ScheduleLimitError) as exc:
            await update.message.reply_text(f"не добавлено: {exc}")
            return
        except IntegrityError:
            session.rollback()
            await update.message.reply_text(f"на {time_of_day} уже есть пробник")
            return
    sync_jobs(context.bot_data["scheduler"], session_factory)
    await update.message.reply_text(f"добавлено на {time_of_day} UTC", reply_markup=kb.main_menu())


async def delete_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    _, _, account_id_s, schedule_id_s = update.callback_query.data.split(":")
    account_id, schedule_id = int(account_id_s), int(schedule_id_s)
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        schedule = session.get(ProbeSchedule, schedule_id)
        if schedule is not None:
            session.delete(schedule)
            session.commit()
        schedules = session.query(ProbeSchedule).filter_by(account_id=account_id, enabled=True).all()
    sync_jobs(context.bot_data["scheduler"], session_factory)
    await edit_or_send(update, "Расписание проверок (UTC):", reply_markup=kb.schedule_menu(account_id, schedules))


# ---------------------------------------------------------------------------
# bulk "probe everything now" job — see jobs.py for what this demonstrates
# ---------------------------------------------------------------------------


async def start_probe_all_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    session_factory = context.bot_data["session_factory"]
    with session_factory() as session:
        accounts = {acc.label: acc.id for acc in session.query(AIAccount).filter_by(enabled=True).all()}
    if not accounts:
        await edit_or_send(update, "нет включённых аккаунтов", reply_markup=kb.main_menu())
        return

    job = jobs.create_job("Проверка всех аккаунтов", list(accounts))
    context.chat_data["active_job_id"] = job.id
    message = await update.callback_query.edit_message_text(job.render(), reply_markup=kb.cancel_job_menu(job.id))

    async def _edit(text: str) -> None:
        await context.bot.edit_message_text(
            chat_id=message.chat_id, message_id=message.message_id, text=text, reply_markup=kb.cancel_job_menu(job.id)
        )

    editor = await jobs.debounced_editor(_edit)

    async def on_progress(j: jobs.Job) -> None:
        await editor(j.render())

    async def run_one(label: str) -> str:
        with session_factory() as session:
            account = session.get(AIAccount, accounts[label])
            run = await run_probe(session, account, "ping")
            return f"{run.latency_ms} мс" if run.success else (run.error or "ошибка")

    await jobs.run_workers(job, list(accounts), run_one, on_progress=on_progress)
    context.chat_data.pop("active_job_id", None)
    await context.bot.edit_message_text(chat_id=message.chat_id, message_id=message.message_id, text=job.render(), reply_markup=None)
    jobs.drop_job(job.id)


async def cancel_job_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    job_id = update.callback_query.data.split(":", 2)[2]
    jobs.request_cancel(job_id)
    await update.callback_query.answer("Отменяю…")


# ---------------------------------------------------------------------------
# custom task (README Task Type "Кастом") — one AI call, live status, REAL
# cancellation (unlike probe_all's cooperative between-workers check, this
# cancels the actual in-flight asyncio.Task via jobs.attach_task)
# ---------------------------------------------------------------------------


async def prompt_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    await update.callback_query.answer()
    set_waiting(context, "task")
    await edit_or_send(update, "Пришли текст задачи одним сообщением. Аккаунт выбирается автоматически.")


async def _handle_task_input(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    session_factory = context.bot_data["session_factory"]

    try:
        account_label = await _peek_account_label(session_factory)
    except NoAccountAvailableError as exc:
        await update.message.reply_text(f"не выполнено: {exc}")
        return

    job = jobs.create_job("Задача", [account_label])
    job.workers[account_label].state = "running"
    context.chat_data["active_job_id"] = job.id
    message = await update.message.reply_text(job.render(), reply_markup=kb.cancel_job_menu(job.id))

    task = asyncio.ensure_future(run_custom_task(session_factory, DEFAULT_TASK_PROVIDER, prompt))
    jobs.attach_task(job.id, task)
    try:
        _, result = await task
    except asyncio.CancelledError:
        job.workers[account_label].state = "cancelled"
        final_text = job.render()
    else:
        if result.success:
            job.workers[account_label].state = "done"
            final_text = result.text.strip() or "(пустой ответ)"
            if len(final_text) > TELEGRAM_MAX_MESSAGE_LEN:
                final_text = final_text[: TELEGRAM_MAX_MESSAGE_LEN - 20] + "\n…(обрезано)"
        else:
            job.workers[account_label].state = "failed"
            job.workers[account_label].detail = result.error or "ошибка"
            final_text = job.render()

    context.chat_data.pop("active_job_id", None)
    await context.bot.edit_message_text(chat_id=message.chat_id, message_id=message.message_id, text=final_text, reply_markup=None)
    jobs.drop_job(job.id)


async def _peek_account_label(session_factory) -> str:
    """Raise NoAccountAvailableError early (before creating a job/message) if the pool
    is empty, instead of only finding out after the job UI is already on screen."""
    with session_factory() as session:
        account = pick_account(session, DEFAULT_TASK_PROVIDER)
        if account is None:
            raise NoAccountAvailableError(f"нет включённых аккаунтов '{DEFAULT_TASK_PROVIDER}'")
        return account.label


# ---------------------------------------------------------------------------
# free-text input: consumes a pending waiting_for, or queues an interjection
# into whatever job is currently running for this chat
# ---------------------------------------------------------------------------


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update, context):
        return
    text = update.message.text or ""

    waiting = pop_waiting(context)
    if waiting is not None:
        kind = waiting["kind"]
        if kind == "add_account":
            await _handle_add_account_input(update, context, text)
        elif kind == "set_proxy":
            await _handle_set_proxy_input(update, context, text, int(waiting["account_id"]))
        elif kind == "add_schedule":
            await _handle_add_schedule_input(update, context, text, int(waiting["account_id"]))
        elif kind == "task":
            await _handle_task_input(update, context, text)
        return

    active_job_id = context.chat_data.get("active_job_id")
    if active_job_id and jobs.push_interjection(active_job_id, text):
        await update.message.reply_text("Принято, будет видно в статусе задачи.")
        return

    await update.message.reply_text("Не понял. /start — открыть меню.")


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
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern="^menu:main$"))
    application.add_handler(CallbackQueryHandler(dismiss_cb, pattern=f"^{CB_OK}$"))
    application.add_handler(CallbackQueryHandler(show_accounts_menu, pattern="^menu:accounts$"))
    application.add_handler(CallbackQueryHandler(prompt_add_account, pattern="^acc:add$"))
    application.add_handler(CallbackQueryHandler(prompt_set_proxy, pattern=r"^acc:proxy:\d+$"))
    application.add_handler(CallbackQueryHandler(probe_account_now, pattern=r"^acc:probe:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_account, pattern=r"^acc:toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_delete_account, pattern=r"^acc:delconfirm:\d+$"))
    application.add_handler(CallbackQueryHandler(do_delete_account, pattern=r"^acc:delete:\d+$"))
    application.add_handler(CallbackQueryHandler(show_account_detail, pattern=r"^acc:\d+$"))
    application.add_handler(CallbackQueryHandler(prompt_add_schedule, pattern=r"^sch:add:\d+$"))
    application.add_handler(CallbackQueryHandler(delete_schedule, pattern=r"^sch:del:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(show_schedule_menu, pattern=r"^sch:\d+$"))
    application.add_handler(CallbackQueryHandler(start_probe_all_job, pattern="^job:probe_all$"))
    application.add_handler(CallbackQueryHandler(cancel_job_cb, pattern=r"^job:cancel:.+$"))
    application.add_handler(CallbackQueryHandler(prompt_add_task, pattern="^task:new$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    scheduler.start()
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
