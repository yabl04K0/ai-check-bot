"""Главное меню + общие мелочи (dismiss-кнопка на уведомлениях)."""

from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.ai_chat import agent_activity
from app.ai_chat.sessions import sessions_with_live_status
from app.bot.access_control import is_admin
from app.bot.formatting import render_job_status_line
from app.bot.handlers.ai_chat import reset_stale_chat
from app.bot.handlers.start import MAIN_MENU_TEXT
from app.bot.keyboards import back_button, main_menu, nav_row
from app.db.models import Job, JobStatus, ProviderName
from app.db.session import get_session
from app.providers import circuit_breaker
from app.providers.quota import account_quota_estimate_for, account_usage_summary


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("flow", None)
    # awaiting=="ai_chat" не одноразовый — уход отсюда без явного "🚪 Закрыть
    # чат" раньше оставлял AiChatSession висеть в БД активной навсегда, а
    # следующее свободное сообщение пользователя в ЛЮБОМ другом месте бота
    # тихо уходило в эту старую сессию (см. аудит меню). reset_stale_chat
    # закрывает сессию ТОЛЬКО если awaiting=="ai_chat" — общий сброс
    # awaiting (был и для любых других значений, например ожидания ключа
    # провайдера) остаётся отдельной безусловной строкой ниже.
    reset_stale_chat(context, update.effective_user.id)
    context.user_data.pop("awaiting", None)
    markup = main_menu(is_admin=is_admin(update, context))
    try:
        await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=markup)
    except TelegramError:
        # Сообщение могло устареть/быть удалено, либо редкий "message is
        # not modified" при быстром повторном тапе — самая частая кнопка
        # во всём боте (🏠 Меню есть почти в каждом nav_row) не должна
        # тихо зависать без реакции, шлём меню новым сообщением.
        await update.effective_chat.send_message(MAIN_MENU_TEXT, reply_markup=markup)


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def limits_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Самооценка расхода токенов за 5ч/неделю по каждому провайдеру и
    аккаунту — см. app.providers.quota.account_usage_summary. Не %, не
    официальные данные Anthropic у большинства провайдеров (кроме
    claude_code:primary, см. app.providers.claude_code_usage) — только
    то, что бот сам отправил, честно посчитанное."""
    registry = context.application.bot_data["provider_registry"]
    lines = [
        "📊 ЛИМИТЫ",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        "Самооценка бота — не % от Anthropic, официального API нет (кроме 🧪 отметок).",
        "",
    ]
    any_usage = False
    for name in registry.all():
        summary = account_usage_summary(name)
        if not summary:
            continue
        any_usage = True
        lines.append(f"▸ {name.value}")
        for label, (five_h, week) in sorted(summary.items(), key=lambda kv: kv[0] or ""):
            label_text = label or "(без метки)"
            health = " 🔴 не отвечает" if circuit_breaker.is_open(name, label or "primary") else ""
            usage = f"5ч: {_fmt_tokens(five_h)} · неделя: {_fmt_tokens(week)}"
            real_note = ""
            if name == ProviderName.CLAUDE_CODE and (label or "primary") == "primary":
                estimate = account_quota_estimate_for(registry, name, "primary")
                if not estimate.is_estimate and estimate.used_pct is not None:
                    real_note = f" · 🧪 реально: {estimate.used_pct:.0f}%"
            lines.append(f"    {label_text} — {usage}{real_note}{health}")
    if not any_usage:
        lines.append("Пока пусто — ни одного вызова ИИ ещё не залогировано.")
    return "\n".join(lines)


async def show_limits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(limits_text(context), reply_markup=InlineKeyboardMarkup([[back_button()]]))


ACTIVE_JOB_STATUSES = (
    JobStatus.RUNNING,
    JobStatus.PAUSED_MANUAL,
    JobStatus.PAUSED_QUESTION,
    JobStatus.PAUSED_QUOTA,
)


def activity_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    lines = ["🤖 Активность", ""]

    with get_session() as session:
        jobs = session.scalars(select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES))).all()
        job_lines = [
            f"{render_job_status_line(job)} ({job.progress_step}/{job.progress_total})" for job in jobs
        ]
    lines.append("📋 Задачи:")
    lines.extend(job_lines or ["Нет активных задач."])
    lines.append("")

    agents = agent_activity.active()
    agent_lines = [
        f"🔧 {entry.project} — {entry.task[:60]} — {int(entry.elapsed_seconds())}s" for entry in agents
    ]
    lines.append("🤖 Агенты:")
    lines.extend(agent_lines or ["Нет запущенных агентов."])
    lines.append("")

    chats = sessions_with_live_status()
    chat_lines = [f"🗨 #{chat.id} — {chat.status_detail}" for chat in chats]
    lines.append("🗨 Чаты:")
    lines.extend(chat_lines or ["Нет активных ИИ-чатов."])

    return "\n".join(lines)


async def show_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(activity_text(context), reply_markup=InlineKeyboardMarkup([nav_row()]))


async def dismiss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — сообщение могло уже устареть
        pass


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка-индикатор пагинации (например "3/12" в paginate_rows) —
    просто гасит спиннер загрузки на тапе, действия не производит."""
    await update.callback_query.answer()


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(show_limits, pattern=r"^menu:limits$"))
    application.add_handler(CallbackQueryHandler(show_activity, pattern=r"^menu:activity$"))
    application.add_handler(CallbackQueryHandler(dismiss, pattern=r"^dismiss$"))
    application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))
