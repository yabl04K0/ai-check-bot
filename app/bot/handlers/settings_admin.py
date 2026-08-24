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
from app.providers.accounts_store import (
    AccountEntry,
    add_extra_account,
    list_extra_accounts,
    list_extra_secrets,
    remove_extra_account,
)
from app.providers.ai_autonomy import (
    ai_command_auto_approve_enabled,
    ai_github_token_access_enabled,
    set_ai_command_auto_approve,
    set_ai_github_token_access,
)
from app.providers.base import ProviderError
from app.providers.key_store import clear_key_override, env_default_key, get_key_override, set_key_override
from app.providers.registry import ProviderRegistry
from app.scheduler.decision import decide_autocheck_action
from app.tasks.types import TASK_TYPE_LABELS

TOKEN_ACCESS_DISCLAIMER = (
    "⚠️ Дисклеймер\n\n"
    "Включая это, ты даёшь CLI-агенту (Cursor и т.п.) реальный GITHUB_TOKEN "
    "в окружении процесса. Промпты бота просят его только вернуть diff "
    "текстом (см. README), но agentic CLI в принципе может сделать больше "
    "запрошенного — с токеном в окружении у него физически ЕСТЬ права на "
    "git push / gh CLI команды от твоего имени, если он сам решит их "
    "выполнить. Без этого тумблера у него токена нет вообще, что бы он ни "
    "решил.\n\n"
    "Точно включить?"
)

AUTO_APPROVE_DISCLAIMER = (
    "⚠️ Дисклеймер\n\n"
    "Пока включён доступ ИИ к GITHUB_TOKEN, каждый запуск задачи по "
    "умолчанию требует отдельного тапа «✅ Разрешить» перед стартом — "
    "как в приложениях для вайб-кодинга, которые спрашивают подтверждение "
    "перед выполнением команд. Включая автоодобрение, ты отключаешь эту "
    "проверку: задачи будут стартовать сразу, без дополнительного "
    "подтверждения, пока доступ к токену включён.\n\n"
    "Точно включить автоодобрение?"
)


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _refresh_settings(query, context)


async def _refresh_settings(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Общий рендер ⚙️ Настройки — edit_message_text принимает reply_markup
    только как keyword (2-й позиционный аргумент реального API — parse_mode,
    не reply_markup), так что _settings_view() нельзя просто распаковывать
    звёздочкой в edit_message_text(*...) — раньше именно так и было сделано
    во всех вызывающих местах ниже, из-за чего любое нажатие в Настройках
    падало с BadRequest("Unsupported parse_mode")."""
    text, markup = _settings_view(context)
    await query.edit_message_text(text, reply_markup=markup)


def _settings_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    settings = context.application.bot_data["settings"]
    autocheck_on = context.application.bot_data.get("autocheck_enabled_override", settings.autocheck.enabled)
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    token_access_on = ai_github_token_access_enabled()
    auto_approve_on = ai_command_auto_approve_enabled()

    lines = [
        f"🔔 Авточек глобально: {'вкл' if autocheck_on else 'выкл'}",
        f"   правило: <{settings.autocheck.full_threshold_pct}% квоты→Full, "
        f"<{settings.autocheck.lite_hours_before_reset}ч и <{settings.autocheck.lite_threshold_pct}%→Lite",
        "",
        "🤖 Автономность ИИ:",
        f"  ИИ видит GITHUB_TOKEN: {'вкл ⚠️' if token_access_on else 'выкл (безопасно)'}",
        f"  Автоодобрение команд: {'вкл' if auto_approve_on else 'выкл — каждый запуск подтверждается'}",
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

        if provider.supports_key_entry():
            provider_rows.append(
                [InlineKeyboardButton(f"🔑 Ключ: {name.value}", callback_data=f"set:key:{name.value}")]
            )

    rows = [
        [InlineKeyboardButton(
            f"🔔 Авточек: {'выключить' if autocheck_on else 'включить'}",
            callback_data="set:toggle_autocheck",
        )],
        [InlineKeyboardButton(
            f"🔑 ИИ видит GITHUB_TOKEN: {'выключить' if token_access_on else 'включить'}",
            callback_data="set:toggle_token_access",
        )],
        [InlineKeyboardButton(
            f"✅ Автоодобрение команд: {'выключить' if auto_approve_on else 'включить'}",
            callback_data="set:toggle_auto_approve",
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
    await _refresh_settings(query, context)


async def toggle_token_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выключить — сразу и без вопросов (возврат к безопасному состоянию
    никогда не требует дисклеймера). Включить — только через отдельный
    экран с дисклеймером (см. confirm_token_access)."""
    query = update.callback_query
    if ai_github_token_access_enabled():
        set_ai_github_token_access(False)
        log_action(str(update.effective_user.id), "ai_github_token_access_disabled", "")
        await query.answer("Выключено")
        await _refresh_settings(query, context)
        return

    await query.answer()
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, включить", callback_data="set:confirm_token_access")],
            [back_button("menu:settings")],
        ]
    )
    await query.edit_message_text(TOKEN_ACCESS_DISCLAIMER, reply_markup=markup)


async def confirm_token_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    set_ai_github_token_access(True)
    log_action(str(update.effective_user.id), "ai_github_token_access_enabled", "")
    await query.answer("Включено")
    await _refresh_settings(query, context)


async def toggle_auto_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if ai_command_auto_approve_enabled():
        set_ai_command_auto_approve(False)
        log_action(str(update.effective_user.id), "ai_auto_approve_disabled", "")
        await query.answer("Выключено")
        await _refresh_settings(query, context)
        return

    await query.answer()
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, включить", callback_data="set:confirm_auto_approve")],
            [back_button("menu:settings")],
        ]
    )
    await query.edit_message_text(AUTO_APPROVE_DISCLAIMER, reply_markup=markup)


async def confirm_auto_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    set_ai_command_auto_approve(True)
    log_action(str(update.effective_user.id), "ai_auto_approve_enabled", "")
    await query.answer("Включено")
    await _refresh_settings(query, context)


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
    await _refresh_settings(query, context)


async def refresh_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Явный триггер перечитать auth_status() — сам статус и так живой на
    каждом рендере Настроек, но кнопка даёт понятную обратную связь
    ("проверил именно сейчас"), особенно после логина в другом окне/CLI."""
    query = update.callback_query
    await query.answer("Обновляю статус…")
    await _refresh_settings(query, context)


async def disable_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider_name = ProviderName(query.data.split(":")[-1])
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    registry.disable(provider_name)
    log_action(str(update.effective_user.id), "provider_disabled", provider_name.value)
    await query.answer("Отключено")
    await _refresh_settings(query, context)


async def enable_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider_name = ProviderName(query.data.split(":")[-1])
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    registry.enable(provider_name)
    log_action(str(update.effective_user.id), "provider_enabled", provider_name.value)
    await query.answer("Подключено")
    await _refresh_settings(query, context)


def _key_menu(
    provider_name: ProviderName, *, has_override: bool, extra_accounts: list[AccountEntry]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "🔑 Задать/обновить основной", callback_data=f"set:key_set:{provider_name.value}"
            )
        ]
    ]
    if has_override:
        rows.append(
            [
                InlineKeyboardButton(
                    "🗑 Убрать основной (вернуться к .env)",
                    callback_data=f"set:key_clear:{provider_name.value}",
                )
            ]
        )
    for i, account in enumerate(extra_accounts, start=2):
        rows.append(
            [
                InlineKeyboardButton(
                    f"🗑 Убрать аккаунт #{i}", callback_data=f"set:key_del:{provider_name.value}:{account.id}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("➕ Добавить ещё аккаунт", callback_data=f"set:key_add:{provider_name.value}")]
    )
    rows.append([back_button("menu:settings")])
    return InlineKeyboardMarkup(rows)


async def _render_provider_key(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider_name: ProviderName
) -> None:
    """Общий рендер экрана ключа — без query.answer(), см. _render_token_status
    в app/bot/handlers/github.py для того же паттерна и той же причины."""
    query = update.callback_query
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    provider = registry.get(provider_name)
    has_override = get_key_override(provider_name) is not None
    extra_accounts = list_extra_accounts(provider_name)
    status = provider.auth_status()

    if has_override:
        source = "бот (переопределяет .env)"
    elif status.status == ProviderAccountStatus.CONNECTED:
        source = ".env"
    else:
        source = "не задан"

    lines = [
        f"🔑 {provider_name.value} — аккаунты",
        f"Основной — источник: {source}",
        f"Статус: {status.status.value}" + (f" ({status.detail})" if status.detail else ""),
    ]
    if extra_accounts:
        lines.append(f"Плюс дополнительных: {len(extra_accounts)}")
    lines.append(
        "\n«Задать/обновить основной» и «➕ Добавить ещё аккаунт» применяются сразу, рестарт бота не "
        "нужен. Сообщение с ключом будет сразу удалено ботом из чата после сохранения. При ошибке/квоте "
        "у текущего аккаунта бот сам переходит на следующий по порядку."
    )
    text = "\n".join(lines)
    await query.edit_message_text(
        text, reply_markup=_key_menu(provider_name, has_override=has_override, extra_accounts=extra_accounts)
    )


async def show_provider_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    provider_name = ProviderName(query.data.split(":")[-1])
    await _render_provider_key(update, context, provider_name)


async def prompt_set_provider_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    provider_name = ProviderName(query.data.split(":")[-1])
    context.user_data["awaiting"] = f"provider_key:{provider_name.value}"
    await query.edit_message_text(
        f"🔑 Пришли новый основной API-ключ для {provider_name.value} следующим сообщением.\n\n"
        "Сообщение с ключом будет сразу удалено ботом из чата после сохранения.",
        reply_markup=back_button(f"set:key:{provider_name.value}"),
    )


async def prompt_add_extra_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    provider_name = ProviderName(query.data.split(":")[-1])
    context.user_data["awaiting"] = f"provider_extra_key:{provider_name.value}"
    await query.edit_message_text(
        f"➕ Пришли ключ/токен ЕЩЁ ОДНОГО аккаунта {provider_name.value} следующим сообщением.\n\n"
        "Добавляется в конец очереди — бот переходит на него, только если основной (или "
        "предыдущий по очереди) упрётся в ошибку/квоту. Сообщение будет сразу удалено ботом "
        "из чата после сохранения.",
        reply_markup=back_button(f"set:key:{provider_name.value}"),
    )


def _extract_secret_or_reject(update: Update) -> str | None:
    """Общая валидация для основного ключа и доп. аккаунта — секрет не
    должен быть пустым и не должен содержать пробелов (типичный признак,
    что скопировали не то). Возвращает None, если текст не похож на секрет."""
    text = update.message.text.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    return text


async def receive_provider_key_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")
    if not awaiting or not (
        awaiting.startswith("provider_key:") or awaiting.startswith("provider_extra_key:")
    ):
        return
    context.user_data["awaiting"] = None

    settings = context.application.bot_data["settings"]
    if not (settings.admin_tg_id and update.effective_user.id == settings.admin_tg_id):
        return  # флаг мог остаться от другого юзера, если он был сброшен странно

    is_extra = awaiting.startswith("provider_extra_key:")
    provider_name = ProviderName(awaiting.split(":", 1)[1])

    # Секрет в открытом чате не должен оставаться в истории переписки (см.
    # тот же приём для GitHub-токена в app/bot/handlers/github.py) —
    # чистим сообщение сразу, независимо от того, валиден ли текст.
    secret = _extract_secret_or_reject(update)
    try:
        await update.message.delete()
    except TelegramError:
        pass  # не критично для сохранения секрета, просто не смогли подчистить чат

    if secret is None:
        await context.bot.send_message(
            update.effective_chat.id,
            f"⚠️ Похоже на не тот текст — не сохранён. Открой ⚙️ Настройки → "
            f"🔑 Ключ: {provider_name.value} ещё раз.",
        )
        return

    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    provider = registry.get(provider_name)

    if is_extra:
        add_extra_account(provider_name, secret)
        provider.set_extra_accounts(list_extra_secrets(provider_name))
        log_action(str(update.effective_user.id), "provider_extra_account_added", provider_name.value)
        confirm_text = f"✅ Ещё один аккаунт {provider_name.value} добавлен и уже используется как фолбэк."
    else:
        set_key_override(provider_name, secret)
        provider.update_api_key(secret)
        log_action(str(update.effective_user.id), "provider_key_set_via_bot", provider_name.value)
        confirm_text = f"✅ Основной ключ для {provider_name.value} сохранён и уже используется."

    await context.bot.send_message(
        update.effective_chat.id,
        confirm_text + " Рестарт бота не нужен.",
        reply_markup=back_button("menu:settings"),
    )


async def delete_extra_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, provider_raw, account_id_raw = query.data.split(":")
    provider_name = ProviderName(provider_raw)
    remove_extra_account(provider_name, int(account_id_raw))

    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    registry.get(provider_name).set_extra_accounts(list_extra_secrets(provider_name))

    log_action(str(update.effective_user.id), "provider_extra_account_removed", provider_name.value)
    await query.answer("Убрано")
    await _render_provider_key(update, context, provider_name)


async def clear_provider_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider_name = ProviderName(query.data.split(":")[-1])
    clear_key_override(provider_name)

    settings = context.application.bot_data["settings"]
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    registry.get(provider_name).update_api_key(env_default_key(provider_name, settings.providers))

    log_action(str(update.effective_user.id), "provider_key_override_cleared", provider_name.value)
    await query.answer("Убрано")
    await _render_provider_key(update, context, provider_name)


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
    application.add_handler(
        CallbackQueryHandler(toggle_token_access, pattern=r"^set:toggle_token_access$")
    )
    application.add_handler(
        CallbackQueryHandler(confirm_token_access, pattern=r"^set:confirm_token_access$")
    )
    application.add_handler(
        CallbackQueryHandler(toggle_auto_approve, pattern=r"^set:toggle_auto_approve$")
    )
    application.add_handler(
        CallbackQueryHandler(confirm_auto_approve, pattern=r"^set:confirm_auto_approve$")
    )
    application.add_handler(CallbackQueryHandler(login_provider, pattern=r"^set:login:\w+$"))
    application.add_handler(CallbackQueryHandler(refresh_provider, pattern=r"^set:refresh:\w+$"))
    application.add_handler(CallbackQueryHandler(disable_provider, pattern=r"^set:disable:\w+$"))
    application.add_handler(CallbackQueryHandler(enable_provider, pattern=r"^set:enable:\w+$"))
    application.add_handler(CallbackQueryHandler(show_provider_key, pattern=r"^set:key:\w+$"))
    application.add_handler(CallbackQueryHandler(prompt_set_provider_key, pattern=r"^set:key_set:\w+$"))
    application.add_handler(CallbackQueryHandler(clear_provider_key, pattern=r"^set:key_clear:\w+$"))
    application.add_handler(CallbackQueryHandler(prompt_add_extra_account, pattern=r"^set:key_add:\w+$"))
    application.add_handler(CallbackQueryHandler(delete_extra_account, pattern=r"^set:key_del:\w+:\d+$"))
    application.add_handler(CallbackQueryHandler(show_history_projects, pattern=r"^menu:history$"))
    application.add_handler(CallbackQueryHandler(show_history_for_project, pattern=r"^hist:proj:\d+$"))
    application.add_handler(CallbackQueryHandler(show_admin, pattern=r"^menu:admin$"))
    application.add_handler(CallbackQueryHandler(dry_run, pattern=r"^admin:dry_run$"))
    application.add_handler(CallbackQueryHandler(prompt_broadcast, pattern=r"^admin:broadcast$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=2)
    # Отдельная группа от on_text (group=2) и github.receive_token_text
    # (group=3) — PTB выполняет максимум один хендлер на группу за апдейт,
    # каждый текстовый хендлер сам решает по своему "awaiting", реагировать
    # ли на это конкретное сообщение (см. комментарий в github.py::register).
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_provider_key_text), group=4
    )
