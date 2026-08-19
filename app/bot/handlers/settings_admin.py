"""⚙️ Настройки, 👑 Админка, 🕘 История — разделы, не требующие отдельного файла."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.bot.keyboards import back_button
from app.db.models import HistoryEntry, Job, JobStatus, Project, ProviderAccountStatus, ProviderName, User
from app.db.session import get_session
from app.logging_setup import log_action
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(*_settings_view(context))


def _settings_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    settings = context.application.bot_data["settings"]
    autocheck_on = context.application.bot_data.get("autocheck_enabled_override", settings.autocheck.enabled)
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]

    lines = [
        f"🔔 Авточек глобально: {'вкл' if autocheck_on else 'выкл'}",
        f"   правило: <{settings.autocheck.full_threshold_pct}% квоты→Full, "
        f"<{settings.autocheck.lite_hours_before_reset}ч и <{settings.autocheck.lite_threshold_pct}%→Lite",
        "",
        "🔌 Провайдеры ИИ:",
    ]
    login_rows = []
    for name, provider in registry.all().items():
        status = provider.auth_status()
        detail_suffix = f" ({status.detail})" if status.detail else ""
        lines.append(f"  {name.value}: {status.status.value}{detail_suffix}")
        if provider.supports_login() and status.status != ProviderAccountStatus.CONNECTED:
            login_rows.append(
                [InlineKeyboardButton(f"🔑 Войти: {name.value}", callback_data=f"set:login:{name.value}")]
            )

    rows = [
        [InlineKeyboardButton(
            f"🔔 Авточек: {'выключить' if autocheck_on else 'включить'}",
            callback_data="set:toggle_autocheck",
        )],
        *login_rows,
        [back_button()],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def toggle_autocheck_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    settings = context.application.bot_data["settings"]
    current = context.application.bot_data.get("autocheck_enabled_override", settings.autocheck.enabled)
    context.application.bot_data["autocheck_enabled_override"] = not current
    await query.answer("Ок")
    await query.edit_message_text(*_settings_view(context))


async def login_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Запускаю логин…")
    provider_name = ProviderName(query.data.split(":")[-1])
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    provider = registry.get(provider_name)

    try:
        result = await asyncio.to_thread(provider.login)
        text = ("✅ " if result.success else "ℹ️ ") + result.message
    except ProviderError as exc:
        text = f"❌ {exc}"

    log_action(
        str(update.effective_user.id), "provider_login_attempt", f"{provider_name.value}: {text[:200]}"
    )
    await context.bot.send_message(update.effective_chat.id, text[:4000])
    await query.edit_message_text(*_settings_view(context))


async def show_history_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    if not projects:
        await query.edit_message_text("Проектов пока нет.", reply_markup=back_button())
        return
    rows = [[InlineKeyboardButton(p.name, callback_data=f"hist:proj:{p.id}")] for p in projects]
    rows.append([back_button()])
    await query.edit_message_text("🕘 История — какой проект?", reply_markup=InlineKeyboardMarkup(rows))


async def show_history_for_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        entries = session.scalars(
            select(HistoryEntry)
            .where(HistoryEntry.project_id == project_id)
            .order_by(HistoryEntry.created_at.desc())
            .limit(20)
        ).all()
        name = project.name if project else "?"
        lines = [f"🕘 {name} — прошлые запуски"]
        for e in entries:
            provider = e.provider.value if e.provider else "?"
            lines.append(
                f"{e.created_at:%Y-%m-%d %H:%M} · {e.task_type.value} · {provider}"
                + (f" · {e.commit_url}" if e.commit_url else "")
            )
        if not entries:
            lines.append("(пока пусто)")
    await query.edit_message_text("\n".join(lines), reply_markup=back_button("menu:history"))


async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    settings = context.application.bot_data["settings"]
    is_admin = bool(settings.admin_tg_id and update.effective_user.id == settings.admin_tg_id)
    if not is_admin:
        await query.edit_message_text("Доступно только администратору.", reply_markup=back_button())
        return

    with get_session() as session:
        users_count = session.scalar(select(func.count()).select_from(User))
        jobs_this_week = session.scalar(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.DONE)
        )
        by_provider = session.execute(
            select(Job.provider, func.count()).where(Job.provider.is_not(None)).group_by(Job.provider)
        ).all()

    lines = [
        f"👥 Пользователей: {users_count}",
        f"📊 Задач выполнено всего: {jobs_this_week}",
        "По провайдерам: " + ", ".join(f"{p.value if p else '?'}={c}" for p, c in by_provider) or "—",
        "📢 Рассылка: TODO",
        "🧪 Тестовый прогон (dry-run): TODO",
    ]
    await query.edit_message_text("\n".join(lines), reply_markup=back_button())


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_settings, pattern=r"^menu:settings$"))
    application.add_handler(CallbackQueryHandler(toggle_autocheck_global, pattern=r"^set:toggle_autocheck$"))
    application.add_handler(CallbackQueryHandler(login_provider, pattern=r"^set:login:\w+$"))
    application.add_handler(CallbackQueryHandler(show_history_projects, pattern=r"^menu:history$"))
    application.add_handler(CallbackQueryHandler(show_history_for_project, pattern=r"^hist:proj:\d+$"))
    application.add_handler(CallbackQueryHandler(show_admin, pattern=r"^menu:admin$"))
