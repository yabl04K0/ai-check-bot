"""⚙️ Настройки, 👑 Админка, 🕘 История — разделы, не требующие отдельного файла."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.keyboards import back_button
from app.db.models import HistoryEntry, Job, JobStatus, Project, ProviderAccountStatus, ProviderName, User
from app.db.session import get_session
from app.logging_setup import log_action
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry
from app.scheduler.decision import decide_autocheck_action
from app.tasks.types import TASK_TYPE_LABELS


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
    provider_rows = []
    for name, provider in registry.all().items():
        status = provider.auth_status()
        disabled = registry.is_disabled(name)

        if disabled:
            lines.append(f"  {name.value}: отключено вручную")
            enable_button = InlineKeyboardButton(
                f"🔌 Подключить обратно: {name.value}", callback_data=f"set:enable:{name.value}"
            )
            provider_rows.append([enable_button])
            continue

        detail_suffix = f" ({status.detail})" if status.detail else ""
        lines.append(f"  {name.value}: {status.status.value}{detail_suffix}")

        if status.status == ProviderAccountStatus.CONNECTED:
            refresh_button = InlineKeyboardButton(
                f"🔄 Обновить: {name.value}", callback_data=f"set:refresh:{name.value}"
            )
            disable_button = InlineKeyboardButton(
                f"🔌 Отключить: {name.value}", callback_data=f"set:disable:{name.value}"
            )
            provider_rows.append([refresh_button, disable_button])
        elif provider.supports_login():
            provider_rows.append(
                [InlineKeyboardButton(f"🔑 Войти: {name.value}", callback_data=f"set:login:{name.value}")]
            )

    rows = [
        [InlineKeyboardButton(
            f"🔔 Авточек: {'выключить' if autocheck_on else 'включить'}",
            callback_data="set:toggle_autocheck",
        )],
        *provider_rows,
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


async def refresh_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Явный триггер перечитать auth_status() — сам статус и так живой на
    каждом рендере Настроек, но кнопка даёт понятную обратную связь
    ("проверил именно сейчас"), особенно после логина в другом окне/CLI."""
    query = update.callback_query
    await query.answer("Обновляю статус…")
    await query.edit_message_text(*_settings_view(context))


async def disable_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider_name = ProviderName(query.data.split(":")[-1])
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    registry.disable(provider_name)
    log_action(str(update.effective_user.id), "provider_disabled", provider_name.value)
    await query.answer("Отключено")
    await query.edit_message_text(*_settings_view(context))


async def enable_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider_name = ProviderName(query.data.split(":")[-1])
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    registry.enable(provider_name)
    log_action(str(update.effective_user.id), "provider_enabled", provider_name.value)
    await query.answer("Подключено")
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


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = context.application.bot_data["settings"]
    return bool(settings.admin_tg_id and update.effective_user.id == settings.admin_tg_id)


async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(update, context):
        await query.edit_message_text("Доступно только администратору.", reply_markup=back_button())
        return

    with get_session() as session:
        users_count = session.scalar(select(func.count()).select_from(User))
        jobs_done_total = session.scalar(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.DONE)
        )
        by_provider = session.execute(
            select(Job.provider, func.count()).where(Job.provider.is_not(None)).group_by(Job.provider)
        ).all()

    by_provider_text = ", ".join(f"{p.value if p else '?'}={c}" for p, c in by_provider) or "—"
    lines = [
        f"👥 Пользователей: {users_count}",
        f"📊 Задач выполнено всего: {jobs_done_total}",
        "По провайдерам: " + by_provider_text,
    ]
    rows = [
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin:broadcast")],
        [InlineKeyboardButton("🧪 Тестовый прогон (dry-run)", callback_data="admin:dry_run")],
        [back_button()],
    ]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def dry_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает, что сделала бы автопроверка по квоте ПРЯМО СЕЙЧАС, не
    ставя ничего в очередь — та же decide_autocheck_action, что и
    настоящий тик планировщика (см. app/scheduler/autocheck.py)."""
    query = update.callback_query
    await query.answer()
    if not _is_admin(update, context):
        await query.edit_message_text("Доступно только администратору.", reply_markup=back_button())
        return

    settings = context.application.bot_data["settings"]
    enabled = context.application.bot_data.get("autocheck_enabled_override", settings.autocheck.enabled)
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    decision = decide_autocheck_action(settings.autocheck, enabled=enabled, registry=registry)

    lines = ["🧪 Dry-run автопроверки", f"Решение: {decision.reason}"]
    if decision.would_run:
        with get_session() as session:
            projects = session.scalars(select(Project).where(Project.autocheck_enabled.is_(True))).all()
        label = TASK_TYPE_LABELS.get(decision.task_type, decision.task_type)
        if not projects:
            lines.append(f"Тип: {label} — но нет проектов с включённым авточеком, по факту не запустится.")
        else:
            names = ", ".join(p.name for p in projects)
            lines.append(f"Запустился бы {label} на: {names}")
    else:
        lines.append("Ничего не запустится.")

    await query.edit_message_text("\n".join(lines), reply_markup=back_button())


async def prompt_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_admin(update, context):
        await query.edit_message_text("Доступно только администратору.", reply_markup=back_button())
        return
    context.user_data["awaiting"] = "broadcast"
    await query.edit_message_text(
        "📢 Отправь текст рассылки — уйдёт всем известным пользователям бота.",
        reply_markup=back_button(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "broadcast":
        return
    context.user_data["awaiting"] = None
    if not _is_admin(update, context):
        return  # флаг мог остаться от другого юзера, если он был сброшен странно — не рассылаем без прав

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Пустой текст, рассылка не отправлена.")
        return

    with get_session() as session:
        tg_ids = session.scalars(select(User.tg_id)).all()

    sent = failed = 0
    for tg_id in tg_ids:
        try:
            await context.bot.send_message(tg_id, f"📢 {text}")
            sent += 1
        except TelegramError:
            failed += 1

    log_action(str(update.effective_user.id), "broadcast", f"sent={sent} failed={failed}")
    await update.message.reply_text(f"✅ Рассылка отправлена: {sent} успешно, {failed} не удалось.")


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_settings, pattern=r"^menu:settings$"))
    application.add_handler(CallbackQueryHandler(toggle_autocheck_global, pattern=r"^set:toggle_autocheck$"))
    application.add_handler(CallbackQueryHandler(login_provider, pattern=r"^set:login:\w+$"))
    application.add_handler(CallbackQueryHandler(refresh_provider, pattern=r"^set:refresh:\w+$"))
    application.add_handler(CallbackQueryHandler(disable_provider, pattern=r"^set:disable:\w+$"))
    application.add_handler(CallbackQueryHandler(enable_provider, pattern=r"^set:enable:\w+$"))
    application.add_handler(CallbackQueryHandler(show_history_projects, pattern=r"^menu:history$"))
    application.add_handler(CallbackQueryHandler(show_history_for_project, pattern=r"^hist:proj:\d+$"))
    application.add_handler(CallbackQueryHandler(show_admin, pattern=r"^menu:admin$"))
    application.add_handler(CallbackQueryHandler(dry_run, pattern=r"^admin:dry_run$"))
    application.add_handler(CallbackQueryHandler(prompt_broadcast, pattern=r"^admin:broadcast$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=2)
