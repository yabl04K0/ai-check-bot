"""⚙️ Настройки, 👑 Админка, 🕘 История — разделы, не требующие отдельного файла."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown

from app.bot.access_control import is_admin
from app.bot.keyboards import back_button, confirm_row, nav_row, paginate_rows
from app.db.models import (
    HistoryEntry,
    Job,
    JobStatus,
    Project,
    ProviderAccountStatus,
    ProviderName,
    ProxyAssignment,
    ProxyPoolEntry,
    ProxyPoolStatus,
    ProxyProtocol,
    User,
)
from app.db.session import get_session
from app.logging_setup import log_action
from app.providers import circuit_breaker
from app.providers.account_notes import get_note, set_note
from app.providers.accounts_store import (
    AccountEntry,
    add_extra_account,
    list_extra_accounts,
    list_extra_secrets,
    remove_extra_account,
)
from app.providers.agent_permissions import (
    can_edit_code,
    can_push_github,
    set_can_edit_code,
    set_can_push_github,
)
from app.providers.ai_autonomy import (
    ai_command_auto_approve_enabled,
    ai_github_token_access_enabled,
    ai_native_agents_enabled,
    ai_show_limits_to_model_enabled,
    set_ai_command_auto_approve,
    set_ai_github_token_access,
    set_ai_native_agents_enabled,
    set_ai_show_limits_to_model,
)
from app.providers.base import ProviderError
from app.providers.custom_api import (
    AUTH_STYLES,
    RESPONSE_FORMATS,
    clear_config,
    detect_provider_name,
    get_config,
    known_account_labels,
    set_auth_style,
    set_config,
    set_response_format,
)
from app.providers.key_store import clear_key_override, env_default_key, get_key_override, set_key_override
from app.providers.model_store import get_model_override, set_model_override, supports_model_override
from app.providers.quota import account_quota_estimate_for
from app.providers.registry import ProviderRegistry
from app.providers.thinking import LEVELS as THINKING_LEVELS
from app.providers.thinking import set_thinking_level, thinking_level
from app.providers.tiers import (
    TIER_CYCLE,
    TIER_ICON,
    TIER_RU_NAME,
    all_known_accounts,
    all_tier_assignments,
    delegation_mode_enabled,
    get_tier,
    set_delegation_mode,
    set_tier,
)
from app.proxies.consumers import active_consumers
from app.proxies.manual_import import add_manual_proxies
from app.proxies.mecelium_import import MeCeliumUnavailableError, import_top_proxies
from app.proxies.pool import assign_proxy
from app.proxies.xray_bridge import restart_bridge
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

NATIVE_AGENTS_DISCLAIMER = (
    "⚠️ Дисклеймер\n\n"
    "Включая это, ты разрешаешь 🗨 ИИ-чату (инструмент run_native_agent, "
    "только при полном доступе к чату) запускать НАСТОЯЩИЕ агенты Claude "
    "Code — не текстовый ответ, а CLI с реальным доступом к файлам/bash "
    "ВНУТРИ выбранного проекта (--permission-mode bypassPermissions, без "
    "единого запроса подтверждения от самого CLI — non-interactive режим "
    "физически не может ждать твоего ответа).\n\n"
    "Автоодобрение команд (см. выше) решает, спросит ли бот тебя перед "
    "КАЖДЫМ конкретным запуском кнопкой «✅ Разрешить» — выключено "
    "(по умолчанию) спросит, включено — агент стартует сразу.\n\n"
    "Точно включить?"
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
    native_agents_on = ai_native_agents_enabled()

    lines = [
        "⚙️ НАСТРОЙКИ",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        f"🔔 Авточек глобально: {'вкл' if autocheck_on else 'выкл'}",
        f"   правило: <{settings.autocheck.full_threshold_pct}% квоты→Full, "
        f"<{settings.autocheck.lite_hours_before_reset}ч и <{settings.autocheck.lite_threshold_pct}%→Lite",
        "",
        "🤖 Автономность ИИ:",
        f"  ИИ видит GITHUB_TOKEN: {'вкл ⚠️' if token_access_on else 'выкл (безопасно)'}",
        f"  Автоодобрение команд: {'вкл' if auto_approve_on else 'выкл — каждый запуск подтверждается'}",
        f"  Настоящие агенты (файлы/bash): {'вкл ⚠️' if native_agents_on else 'выкл (безопасно)'}",
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
        [InlineKeyboardButton(
            f"🤖 Настоящие агенты: {'выключить' if native_agents_on else 'включить'}",
            callback_data="set:toggle_native_agents",
        )],
        *provider_rows,
        [InlineKeyboardButton("🌐 Прокси", callback_data="set:proxies")],
        [InlineKeyboardButton("🎚 Приоритеты аккаунтов", callback_data="set:tiers")],
        [InlineKeyboardButton("🤖 Настройки агентов", callback_data="set:agents")],
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


async def toggle_native_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выключить — сразу и без вопросов, как остальные тумблеры этого
    модуля (возврат к безопасному состоянию не требует дисклеймера).
    Включить — только через отдельный экран с дисклеймером (см.
    confirm_native_agents) — это самый рискованный тумблер файла, реальный
    доступ к файлам/bash, а не просто текст промпта."""
    query = update.callback_query
    if ai_native_agents_enabled():
        set_ai_native_agents_enabled(False)
        log_action(str(update.effective_user.id), "ai_native_agents_disabled", "")
        await query.answer("Выключено")
        await _refresh_settings(query, context)
        return

    await query.answer()
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, включить", callback_data="set:confirm_native_agents")],
            [back_button("menu:settings")],
        ]
    )
    await query.edit_message_text(NATIVE_AGENTS_DISCLAIMER, reply_markup=markup)


async def confirm_native_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    set_ai_native_agents_enabled(True)
    log_action(str(update.effective_user.id), "ai_native_agents_enabled", "")
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
    await query.answer()
    provider_name = ProviderName(query.data.split(":")[-1])
    await query.edit_message_text(
        f"🔌 Отключить провайдера {provider_name.value}? Подключить обратно можно в любой момент.",
        reply_markup=InlineKeyboardMarkup(
            [confirm_row(f"set:disable_yes:{provider_name.value}", "menu:settings")]
        ),
    )


async def disable_provider_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


_STATUS_ICON = {ProxyPoolStatus.ACTIVE: "🟢", ProxyPoolStatus.DEAD: "⚫"}


PROXIES_PAGE_SIZE = 20


def _proxies_view(
    context: ContextTypes.DEFAULT_TYPE, *, prefix: str | None = None, page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    with get_session() as session:
        pool = session.scalars(select(ProxyPoolEntry).order_by(ProxyPoolEntry.import_score.desc())).all()
        assignments = {a.proxy_id: a for a in session.scalars(select(ProxyAssignment)).all()}

        active = [p for p in pool if p.status == ProxyPoolStatus.ACTIVE]
        dead = [p for p in pool if p.status == ProxyPoolStatus.DEAD]
        free = [p for p in active if p.id not in assignments]

        # Раньше был жёсткий срез pool[:20] с "…и ещё N" текстом вместо
        # настоящей пагинации — единственный крупный список бота без неё;
        # прокси за 20-й позицией были навсегда невидимы из бота (см.
        # аудит меню).
        total_pages = max(1, -(-len(pool) // PROXIES_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        page_pool = pool[page * PROXIES_PAGE_SIZE : page * PROXIES_PAGE_SIZE + PROXIES_PAGE_SIZE]
        page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""

        lines = [] if not prefix else [prefix, ""]
        lines.append(f"🌐 Прокси — пул: {len(pool)}{page_note}")
        lines.append(
            f"Активных: {len(active)} (занято {len(active) - len(free)}, свободно {len(free)}), "
            f"мёртвых: {len(dead)}"
        )
        for p in page_pool:
            assignment = assignments.get(p.id)
            icon = "🔒" if assignment else _STATUS_ICON.get(p.status, "?")
            consumer_note = f" → {assignment.provider.value}:{assignment.account_label}" if assignment else ""
            lines.append(f"{icon} {p.host}:{p.port} ({p.protocol.value}){consumer_note}")

    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"set:proxies:page:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"set:proxies:page:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔄 Импортировать из MeCelium", callback_data="set:proxies:import")])
    rows.append([InlineKeyboardButton("✍️ Добавить вручную", callback_data="set:proxies:add")])
    rows.append(nav_row("menu:settings"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    text, markup = _proxies_view(context, page=page)
    await query.edit_message_text(text, reply_markup=markup)


async def show_proxies_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await show_proxies(update, context, page=page)


def _import_proxies_blocking(mecelium_db_path, registry: ProviderRegistry) -> str:
    try:
        with get_session() as session:
            imported = import_top_proxies(session, mecelium_db_path, limit=10)
            for consumer in active_consumers(registry):
                assign_proxy(session, consumer)
            session.commit()
    except MeCeliumUnavailableError as exc:
        return f"⚠️ Не удалось прочитать MeCelium: {exc}"
    icon = "✅" if imported else "ℹ️"
    return f"{icon} Импортировано/обновлено {len(imported)} прокси из MeCelium."


async def import_proxies_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Импортирую…")
    settings = context.application.bot_data["settings"]
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]

    if settings.mecelium_db_path is None:
        prefix = "⚠️ MECELIUM_DB_PATH не задан."
    else:
        # asyncio.to_thread — sqlite3.connect к MECELIUM_DB_PATH (см.
        # app.proxies.mecelium_import) раньше шёл прямо в event loop, как
        # login_provider уже делает для похожей блокирующей операции;
        # недоступный путь (сетевой диск и т.п.) замораживал бы бота
        # целиком на время подключения (см. аудит меню).
        prefix = await asyncio.to_thread(_import_proxies_blocking, settings.mecelium_db_path, registry)

    text, markup = _proxies_view(context, prefix=prefix)
    await query.edit_message_text(text, reply_markup=markup)


async def prompt_add_proxies_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "proxies_manual_add"
    await query.edit_message_text(
        "✍️ Пришли список прокси одним сообщением, по одному на строку:\n"
        "`host:port` или `host:port:protocol` (protocol — socks4/socks5/http/https, "
        "по умолчанию socks5).",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([nav_row("set:proxies")]),
    )


async def receive_proxies_manual_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "proxies_manual_add":
        return
    context.user_data["awaiting"] = None

    settings = context.application.bot_data["settings"]
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    with get_session() as session:
        rows, failed = add_manual_proxies(session, update.message.text)
        for consumer in active_consumers(registry):
            assign_proxy(session, consumer)
        has_shadowsocks = any(r.protocol == ProxyProtocol.SHADOWSOCKS for r in rows)
        bridge_ok = True
        if has_shadowsocks:
            bridge_config = settings.db_path.parent / "xray_proxy_bridge.json"
            bridge_ok = restart_bridge(session, config_path=bridge_config)
        session.commit()

    icon = "✅" if rows else "⚠️"
    lines = [f"{icon} Добавлено/уже было в пуле: {len(rows)}."]
    if has_shadowsocks and not bridge_ok:
        lines.append(
            "⚠️ Среди них есть shadowsocks, но Xray-бинарник не найден — "
            "задай XRAY_PATH в .env, иначе они не заработают."
        )
    if failed:
        lines.append(f"⚠️ Не распознано {len(failed)} строк(и):")
        lines.extend(failed[:10])
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup([nav_row("set:proxies")])
    )


def _account_quota_note(registry: ProviderRegistry, account) -> str | None:
    estimate = account_quota_estimate_for(registry, account.provider, account.account_label)
    if estimate.used_pct is None:
        return None
    real_note = "" if estimate.is_estimate else " реал."
    reset_note = f", сброс через {estimate.hours_to_reset:.0f}ч" if estimate.hours_to_reset else ""
    return f"{estimate.used_pct:.0f}%{real_note}{reset_note}"


def _tiers_view(
    context: ContextTypes.DEFAULT_TYPE, *, prefix: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    enabled = delegation_mode_enabled()
    accounts = all_known_accounts(registry)
    assignments = all_tier_assignments()

    lines = [] if not prefix else [prefix, ""]
    lines.append(f"🎚 Приоритеты аккаунтов: {'🟢 включено' if enabled else '⚪ выключено'}")
    lines.append(
        "Выключено — все шаги идут через обычного провайдера задачи, как раньше.\n"
        "Включено — шаги ЧЕК/Фичи используют аккаунт нужного тира (👑 Глава — "
        "план/критика, ⚖️ Средний — фиксы/тесты, 🤖 Делегация — параллельный "
        "скан доменов, round-robin по нескольким аккаунтам), если он назначен; "
        "иначе тихий откат на обычного провайдера."
    )
    if accounts:
        lines.append("\nТапни по аккаунту, чтобы сменить его тир по кругу.")
    else:
        lines.append("\nНет подключённых аккаунтов ни у одного провайдера.")

    rows = [
        [
            InlineKeyboardButton(
                f"{'🔴 Выключить' if enabled else '🟢 Включить'} режим приоритетов",
                callback_data="set:tiers:toggle",
            )
        ]
    ]
    for account in accounts:
        tier = assignments.get(account)
        icon = TIER_ICON.get(tier, "➖")
        tier_name = TIER_RU_NAME.get(tier, "не задан")
        label = f"{icon} {account.provider.value}:{account.account_label} — {tier_name}"
        if tier is not None:
            quota_note = _account_quota_note(registry, account)
            if quota_note:
                label += f" ({quota_note})"
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"set:tier_cycle:{account.provider.value}:{account.account_label}",
                )
            ]
        )
    rows.append(nav_row("menu:settings"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_tiers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text, markup = _tiers_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_delegation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выключение — через confirm_row, как disable_provider (структурно
    то же "отключение" с тем же паттерном подтверждения, см. аудит меню:
    это был единственный тумблер-переключатель без него). Включение
    остаётся мгновенным, как enable_provider — включить обратно можно в
    любой момент, это не рискованное действие."""
    query = update.callback_query
    if delegation_mode_enabled():
        await query.answer()
        await query.edit_message_text(
            "🔴 Выключить режим приоритетов? Шаги пайплайнов вернутся к "
            "обычному провайдеру задачи, как до включения.",
            reply_markup=InlineKeyboardMarkup([confirm_row("set:tiers:toggle_yes", "set:tiers")]),
        )
        return
    set_delegation_mode(True)
    log_action(str(update.effective_user.id), "delegation_mode_toggled", "True")
    await query.answer("Включено")
    text, markup = _tiers_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_delegation_mode_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    set_delegation_mode(False)
    log_action(str(update.effective_user.id), "delegation_mode_toggled", "False")
    await query.answer("Выключено")
    text, markup = _tiers_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def cycle_account_tier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    payload = query.data[len("set:tier_cycle:") :]
    provider_str, account_label = payload.split(":", 1)
    provider_name = ProviderName(provider_str)

    next_tier = TIER_CYCLE[get_tier(provider_name, account_label)]
    set_tier(provider_name, account_label, next_tier)
    log_action(
        str(update.effective_user.id),
        "account_tier_set",
        f"{provider_str}:{account_label} -> {next_tier.value if next_tier else 'не задан'}",
    )
    # Тост с новым значением — раньше отвечал пустым answer(), и промах
    # мимо нужного тира можно было заметить только докрутив круг заново
    # (см. аудит меню).
    await query.answer(f"→ {TIER_RU_NAME.get(next_tier, 'не задан')}")
    text, markup = _tiers_view(context)
    await query.edit_message_text(text, reply_markup=markup)


AGENT_PROVIDERS = (ProviderName.CLAUDE_CODE, ProviderName.CURSOR)

THINKING_ICON = {"off": "⚪", "low": "🟡", "medium": "🟠", "high": "🔴"}
THINKING_RU_NAME = {"off": "выключено", "low": "низкий", "medium": "средний", "high": "высокий"}


def _agents_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    level = thinking_level()
    show_limits = ai_show_limits_to_model_enabled()
    lines = [
        "🤖 НАСТРОЙКИ АГЕНТОВ",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        "",
        f"🧠 Уровень мышления: {THINKING_ICON[level]} {THINKING_RU_NAME[level]}",
        "Добавляет инструкцию 'обдумай тщательнее' перед каждым вызовом ИИ — "
        "работает через любого провайдера, включая claude_code (think/ultrathink).",
        "",
        f"📊 ИИ видит свои лимиты: {'🟢 вкл' if show_limits else '⚪ выкл'}",
        "👑 Главный аккаунт (Глава/оркестратор чата/провайдер задачи без тиров) "
        "видит лимиты всегда, независимо от этого тумблера.",
        "",
        "✏️ Правка кода / 🐙 Push в GitHub — по провайдеру, поверх общих тумблеров "
        "в ⚙️ Настройки (те решают 'разрешено ли вообще', эти — какому провайдеру). "
        "🐙 Push по умолчанию только у провайдера с тиром 👑 Глава (см. 🎚 Приоритеты "
        "аккаунтов) — тап по кнопке ниже ставит явный оверрайд поверх этого правила.",
    ]
    rows = [
        [
            InlineKeyboardButton(
                f"🧠 Мышление: {THINKING_RU_NAME[level]} →", callback_data="set:agents:thinking"
            )
        ],
        [
            InlineKeyboardButton(
                f"📊 ИИ видит лимиты: {'выключить' if show_limits else 'включить'}",
                callback_data="set:agents:toggle_limits",
            )
        ],
    ]
    for provider in AGENT_PROVIDERS:
        edit_on = can_edit_code(provider)
        push_on = can_push_github(provider)
        rows.append(
            [
                InlineKeyboardButton(
                    f"✏️ {provider.value}: {'🟢' if edit_on else '⚪'}",
                    callback_data=f"set:agents:edit:{provider.value}",
                ),
                InlineKeyboardButton(
                    f"🐙 {provider.value}: {'🟢' if push_on else '⚪'}",
                    callback_data=f"set:agents:push:{provider.value}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton("➕ Свой API", callback_data="set:customapi")])
    rows.append([InlineKeyboardButton("📋 Список аккаунтов", callback_data="set:accounts_list")])
    rows.append(nav_row("menu:settings"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text, markup = _agents_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def cycle_thinking_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    current = thinking_level()
    next_level = THINKING_LEVELS[(THINKING_LEVELS.index(current) + 1) % len(THINKING_LEVELS)]
    set_thinking_level(next_level)
    log_action(str(update.effective_user.id), "thinking_level_set", next_level)
    await query.answer(f"→ {THINKING_RU_NAME[next_level]}")
    text, markup = _agents_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_show_limits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    new_value = not ai_show_limits_to_model_enabled()
    set_ai_show_limits_to_model(new_value)
    log_action(str(update.effective_user.id), "ai_show_limits_to_model_toggled", str(new_value))
    await query.answer("Включено" if new_value else "Выключено")
    text, markup = _agents_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_can_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider = ProviderName(query.data.rsplit(":", 1)[-1])
    new_value = not can_edit_code(provider)
    set_can_edit_code(provider, new_value)
    log_action(
        str(update.effective_user.id), "agent_can_edit_code_toggled", f"{provider.value}={new_value}"
    )
    await query.answer("Включено" if new_value else "Выключено")
    text, markup = _agents_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_can_push(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    provider = ProviderName(query.data.rsplit(":", 1)[-1])
    new_value = not can_push_github(provider)
    set_can_push_github(provider, new_value)
    log_action(
        str(update.effective_user.id), "agent_can_push_github_toggled", f"{provider.value}={new_value}"
    )
    await query.answer("Включено" if new_value else "Выключено")
    text, markup = _agents_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def send_accounts_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Отправляю…")
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    accounts = all_known_accounts(registry)
    chat_id = update.effective_chat.id
    if not accounts:
        await context.bot.send_message(chat_id, "Нет ни одного подключённого аккаунта.")
        return
    await context.bot.send_message(chat_id, f"📋 Подключённые аккаунты ({len(accounts)}):")
    for account in accounts:
        provider = registry.get(account.provider)
        status = provider.auth_status()
        estimate = account_quota_estimate_for(registry, account.provider, account.account_label)
        limit_note = ""
        if estimate.used_pct is not None:
            source = "реальные данные API" if not estimate.is_estimate else "оценка бота"
            limit_note = f"\nЛимит: {estimate.used_pct:.0f}% использовано ({source})"
        circuit_note = (
            "\n🔴 недавно упал — на паузе, автоматически включится обратно"
            if circuit_breaker.is_open(account.provider, account.account_label)
            else ""
        )
        note = get_note(account.provider, account.account_label)
        note_line = f"\n💬 {note}" if note else ""
        text = (
            f"🔌 {account.provider.value}:{account.account_label}\n"
            f"Статус: {status.status.value}{f' ({status.detail})' if status.detail else ''}"
            f"{limit_note}"
            f"{circuit_note}"
            f"{note_line}"
        )
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"⚙️ Настроить {account.provider.value}",
                        callback_data=f"set:key:{account.provider.value}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💬 Изменить коммент",
                        callback_data=f"set:accnote:{account.provider.value}:{account.account_label}",
                    )
                ],
            ]
        )
        await context.bot.send_message(chat_id, text, reply_markup=markup)


async def prompt_account_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, provider_raw, account_label = query.data.split(":", 3)
    provider = ProviderName(provider_raw)
    context.user_data["awaiting"] = f"accnote:{provider.value}:{account_label}"
    current = get_note(provider, account_label)
    await context.bot.send_message(
        update.effective_chat.id,
        f"💬 {provider.value}:{account_label}\nТекущий коммент: {current or '—'}\n\n"
        "Пришли новый текст коммента следующим сообщением.",
    )


def _custom_api_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["➕ Свой API — неограниченное число аккаунтов на любой OpenAI/Anthropic-совместимый сервис.", ""]
    rows = []
    for label in known_account_labels():
        config = get_config(label)
        status = config.display_name if config.is_configured else "не настроен"
        lines.append(f"{label}: {status}")
        rows.append([InlineKeyboardButton(f"⚙️ {label}: {status}", callback_data=f"set:customapi:{label}")])
    rows.append([InlineKeyboardButton("🔑 Ключи (добавить/убрать аккаунт)", callback_data="set:key:custom")])
    rows.append(nav_row("set:agents"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_custom_api(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text, markup = _custom_api_view(context)
    await query.edit_message_text(text, reply_markup=markup)


def _custom_api_account_view(account_label: str) -> tuple[str, InlineKeyboardMarkup]:
    config = get_config(account_label)
    lines = [
        f"⚙️ custom:{account_label}",
        f"Имя: {config.display_name or '—'}",
        f"URL: {config.base_url or '—'}",
        f"Модель: {config.model or '—'}",
        f"Заголовок авторизации: {config.auth_style}",
        f"Формат ответа: {config.response_format}",
    ]
    rows = [
        [InlineKeyboardButton("✏️ Имя", callback_data=f"set:customapi:name:{account_label}")],
        [InlineKeyboardButton("✏️ Base URL", callback_data=f"set:customapi:url:{account_label}")],
        [InlineKeyboardButton("✏️ Модель", callback_data=f"set:customapi:model:{account_label}")],
        [
            InlineKeyboardButton(
                f"🔑 Заголовок: {config.auth_style} →", callback_data=f"set:customapi:auth:{account_label}"
            )
        ],
        [
            InlineKeyboardButton(
                f"📨 Формат: {config.response_format} →",
                callback_data=f"set:customapi:format:{account_label}",
            )
        ],
    ]
    if config.is_configured:
        rows.append(
            [InlineKeyboardButton("🗑 Очистить", callback_data=f"set:customapi:clear:{account_label}")]
        )
    rows.append(nav_row("set:customapi"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_custom_api_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    account_label = query.data.split(":", 2)[2]
    text, markup = _custom_api_account_view(account_label)
    await query.edit_message_text(text, reply_markup=markup)


async def cycle_custom_api_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    account_label = query.data.split(":", 3)[3]
    current = get_config(account_label).auth_style
    next_style = AUTH_STYLES[(AUTH_STYLES.index(current) + 1) % len(AUTH_STYLES)]
    set_auth_style(account_label, next_style)
    await query.answer(f"→ {next_style}")
    text, markup = _custom_api_account_view(account_label)
    await query.edit_message_text(text, reply_markup=markup)


async def cycle_custom_api_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    account_label = query.data.split(":", 3)[3]
    current = get_config(account_label).response_format
    next_format = RESPONSE_FORMATS[(RESPONSE_FORMATS.index(current) + 1) % len(RESPONSE_FORMATS)]
    set_response_format(account_label, next_format)
    await query.answer(f"→ {next_format}")
    text, markup = _custom_api_account_view(account_label)
    await query.edit_message_text(text, reply_markup=markup)


async def prompt_custom_api_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, field, account_label = query.data.split(":", 3)
    context.user_data["awaiting"] = f"customapi_{field}:{account_label}"
    prompts = {
        "name": (
            "Пришли отображаемое имя сервиса следующим сообщением (например 'MyProvider') — "
            "или пропусти этот шаг и просто задай Base URL: бот попробует определить имя сам "
            "по /models сервиса, дать поправить можно в любой момент."
        ),
        "url": (
            "Пришли base URL следующим сообщением (например https://api.example.com/v1, "
            "без /chat/completions)."
        ),
        "model": "Пришли имя модели по умолчанию следующим сообщением.",
    }
    await context.bot.send_message(
        update.effective_chat.id,
        f"✏️ custom:{account_label}: {prompts[field]}",
        reply_markup=InlineKeyboardMarkup([nav_row(f"set:customapi:{account_label}")]),
    )


async def clear_custom_api_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    account_label = query.data.split(":", 3)[3]
    clear_config(account_label)
    log_action(str(update.effective_user.id), "custom_api_account_cleared", account_label)
    await query.answer("Очищено")
    text, markup = _custom_api_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def receive_custom_api_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting") or ""

    if awaiting.startswith("accnote:"):
        context.user_data["awaiting"] = None
        _, provider_raw, account_label = awaiting.split(":", 2)
        provider = ProviderName(provider_raw)
        text = update.message.text.strip()
        set_note(provider, account_label, text)
        log_action(str(update.effective_user.id), "account_note_set", f"{provider.value}:{account_label}")
        await update.message.reply_text("✅ Коммент сохранён." if text else "✅ Коммент очищен.")
        return

    if not awaiting.startswith("customapi_"):
        return
    context.user_data["awaiting"] = None

    field, account_label = awaiting[len("customapi_") :].split(":", 1)
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Пустой текст, не сохранено.")
        return

    config = get_config(account_label)
    kwargs = {
        "display_name": config.display_name or "",
        "base_url": config.base_url or "",
        "model": config.model or "",
        "auth_style": config.auth_style,
        "response_format": config.response_format,
    }
    kwargs[{"name": "display_name", "url": "base_url", "model": "model"}[field]] = text
    if field == "url" and not kwargs["display_name"]:
        kwargs["display_name"] = detect_provider_name(text) or f"custom:{account_label}"
    if not kwargs["display_name"]:
        kwargs["display_name"] = f"custom:{account_label}"
    set_config(account_label, **kwargs)
    log_action(str(update.effective_user.id), "custom_api_field_set", f"{account_label}.{field}")

    text_out, markup = _custom_api_account_view(account_label)
    await update.message.reply_text(f"✅ Сохранено.\n\n{text_out}", reply_markup=markup)


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
    if supports_model_override(provider_name):
        rows.append(
            [InlineKeyboardButton("🧠 Сменить модель", callback_data=f"set:model_set:{provider_name.value}")]
        )
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
    if supports_model_override(provider_name):
        model_source = " (бот)" if get_model_override(provider_name) else " (.env/по умолчанию)"
        lines.append(f"Модель: {getattr(provider, 'current_model', '?')}{model_source}")
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
    # Стандартная точка посадки после ◀️ Назад со всех awaiting-экранов
    # этого провайдера (ключ/модель/доп.аккаунт) — без сброса здесь уход
    # именно через "Назад" (не через отдельный обработчик, который бы сам
    # сбросил awaiting) оставлял его висеть: следующее произвольное
    # сообщение пользователя в ЛЮБОМ другом месте бота тихо сохранялось бы
    # как новый API-ключ этого провайдера (см. аудит меню).
    context.user_data["awaiting"] = None
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
        reply_markup=InlineKeyboardMarkup([nav_row(f"set:key:{provider_name.value}")]),
    )


async def prompt_set_provider_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    provider_name = ProviderName(query.data.split(":")[-1])
    context.user_data["awaiting"] = f"provider_model:{provider_name.value}"
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    current = getattr(registry.get(provider_name), "current_model", "?")
    # current — свободный текст, который сам когда-то ввёл человек через
    # receive_provider_model_text (только проверка "без пробелов", не
    # markdown-безопасность) — имя вроде "my_model" (нечётное число "_")
    # валит legacy Markdown-парсер Telegram, тот же класс бага, что уже
    # чинили для имён репозиториев (см. аудит меню).
    safe_current = escape_markdown(str(current), version=1)
    await query.edit_message_text(
        f"🧠 Текущая модель {provider_name.value}: {safe_current}\n\n"
        "Пришли новое имя модели следующим сообщением (как в документации провайдера, "
        "например `llama-3.1-8b-instant`). Применяется сразу, рестарт бота не нужен.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([nav_row(f"set:key:{provider_name.value}")]),
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
        reply_markup=InlineKeyboardMarkup([nav_row(f"set:key:{provider_name.value}")]),
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
        reply_markup=InlineKeyboardMarkup([nav_row("menu:settings")]),
    )


async def receive_provider_model_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")
    if not awaiting or not awaiting.startswith("provider_model:"):
        return
    context.user_data["awaiting"] = None

    settings = context.application.bot_data["settings"]
    if not (settings.admin_tg_id and update.effective_user.id == settings.admin_tg_id):
        return  # флаг мог остаться от другого юзера, если он был сброшен странно

    provider_name = ProviderName(awaiting.split(":", 1)[1])
    model = update.message.text.strip()
    if not model or any(ch.isspace() for ch in model):
        await update.message.reply_text("⚠️ Похоже на не тот текст — имя модели не должно содержать пробелов.")
        return

    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    set_model_override(provider_name, model)
    registry.get(provider_name).update_model(model)
    log_action(str(update.effective_user.id), "provider_model_set_via_bot", f"{provider_name.value}: {model}")

    await update.message.reply_text(
        f"✅ Модель {provider_name.value} → {model}. Применилось сразу, рестарт не нужен.",
        reply_markup=InlineKeyboardMarkup([nav_row(f"set:key:{provider_name.value}")]),
    )


async def prompt_delete_extra_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, provider_raw, account_id_raw = query.data.split(":")
    await query.edit_message_text(
        f"🗑 Убрать этот дополнительный аккаунт {provider_raw}?",
        reply_markup=InlineKeyboardMarkup(
            [confirm_row(f"set:key_del_yes:{provider_raw}:{account_id_raw}", f"set:key:{provider_raw}")]
        ),
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


async def prompt_clear_provider_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    provider_name = ProviderName(query.data.split(":")[-1])
    await query.edit_message_text(
        f"🗑 Убрать основной ключ {provider_name.value} и вернуться к .env?",
        reply_markup=InlineKeyboardMarkup(
            [confirm_row(f"set:key_clear_yes:{provider_name.value}", f"set:key:{provider_name.value}")]
        ),
    )


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


async def show_history_projects(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    if not projects:
        await query.edit_message_text("Проектов пока нет.", reply_markup=InlineKeyboardMarkup([nav_row()]))
        return
    rows = [[InlineKeyboardButton(p.name, callback_data=f"hist:proj:{p.id}")] for p in projects]
    page_rows, total_pages = paginate_rows(rows, page, nav_prefix="hist:page")
    page_rows.append(nav_row())
    title = "🕘 История — какой проект?" + (f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else "")
    await query.edit_message_text(title, reply_markup=InlineKeyboardMarkup(page_rows))


async def show_history_projects_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await show_history_projects(update, context, page=page)


HISTORY_PAGE_SIZE = 8


async def show_history_for_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    project_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    with get_session() as session:
        project = session.get(Project, project_id)
        all_entries = session.scalars(
            select(HistoryEntry)
            .where(HistoryEntry.project_id == project_id)
            .order_by(HistoryEntry.created_at.desc())
            .limit(200)
        ).all()
        name = project.name if project else "?"

        total = len(all_entries)
        total_pages = max(1, -(-total // HISTORY_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        entries = all_entries[page * HISTORY_PAGE_SIZE : page * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE]
        page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""

        lines = [f"🕘 {name} — прошлые запуски{page_note}"]
        for e in entries:
            provider = e.provider.value if e.provider else "?"
            lines.append(
                f"{e.created_at:%Y-%m-%d %H:%M} · {e.task_type.value} · {provider}"
                + (f" · {e.commit_url}" if e.commit_url else "")
            )
        if not entries:
            lines.append("(пока пусто)")

    nav = [nav_row("menu:history")]
    if total_pages > 1:
        page_nav = []
        if page > 0:
            page_nav.append(InlineKeyboardButton("◀️", callback_data=f"hist:proj:{project_id}:{page - 1}"))
        page_nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            page_nav.append(InlineKeyboardButton("▶️", callback_data=f"hist:proj:{project_id}:{page + 1}"))
        nav.insert(0, page_nav)
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(nav))


async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update, context):
        await query.edit_message_text(
            "Доступно только администратору.", reply_markup=InlineKeyboardMarkup([nav_row()])
        )
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
    if not is_admin(update, context):
        await query.edit_message_text(
            "Доступно только администратору.", reply_markup=InlineKeyboardMarkup([nav_row("menu:admin")])
        )
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

    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup([nav_row("menu:admin")])
    )


async def prompt_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update, context):
        await query.edit_message_text(
            "Доступно только администратору.", reply_markup=InlineKeyboardMarkup([nav_row("menu:admin")])
        )
        return
    context.user_data["awaiting"] = "broadcast"
    await query.edit_message_text(
        "📢 Отправь текст рассылки — уйдёт всем известным пользователям бота.",
        reply_markup=InlineKeyboardMarkup([nav_row("menu:admin")]),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "broadcast":
        return
    context.user_data["awaiting"] = None
    if not is_admin(update, context):
        return  # флаг мог остаться от другого юзера, если он был сброшен странно — не рассылаем без прав

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(
            "Пустой текст, рассылка не отправлена.",
            reply_markup=InlineKeyboardMarkup([nav_row("menu:admin")]),
        )
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
    await update.message.reply_text(
        f"✅ Рассылка отправлена: {sent} успешно, {failed} не удалось.",
        reply_markup=InlineKeyboardMarkup([nav_row("menu:admin")]),
    )


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
    application.add_handler(
        CallbackQueryHandler(toggle_native_agents, pattern=r"^set:toggle_native_agents$")
    )
    application.add_handler(
        CallbackQueryHandler(confirm_native_agents, pattern=r"^set:confirm_native_agents$")
    )
    application.add_handler(CallbackQueryHandler(login_provider, pattern=r"^set:login:\w+$"))
    application.add_handler(CallbackQueryHandler(refresh_provider, pattern=r"^set:refresh:\w+$"))
    application.add_handler(CallbackQueryHandler(disable_provider, pattern=r"^set:disable:\w+$"))
    application.add_handler(CallbackQueryHandler(disable_provider_yes, pattern=r"^set:disable_yes:\w+$"))
    application.add_handler(CallbackQueryHandler(enable_provider, pattern=r"^set:enable:\w+$"))
    application.add_handler(CallbackQueryHandler(show_proxies, pattern=r"^set:proxies$"))
    application.add_handler(CallbackQueryHandler(show_proxies_page, pattern=r"^set:proxies:page:\d+$"))
    application.add_handler(CallbackQueryHandler(import_proxies_action, pattern=r"^set:proxies:import$"))
    application.add_handler(CallbackQueryHandler(prompt_add_proxies_manual, pattern=r"^set:proxies:add$"))
    application.add_handler(CallbackQueryHandler(show_tiers, pattern=r"^set:tiers$"))
    application.add_handler(CallbackQueryHandler(toggle_delegation_mode, pattern=r"^set:tiers:toggle$"))
    application.add_handler(
        CallbackQueryHandler(toggle_delegation_mode_yes, pattern=r"^set:tiers:toggle_yes$")
    )
    application.add_handler(CallbackQueryHandler(cycle_account_tier, pattern=r"^set:tier_cycle:.+$"))
    application.add_handler(CallbackQueryHandler(show_agents, pattern=r"^set:agents$"))
    application.add_handler(CallbackQueryHandler(cycle_thinking_level, pattern=r"^set:agents:thinking$"))
    application.add_handler(CallbackQueryHandler(toggle_show_limits, pattern=r"^set:agents:toggle_limits$"))
    application.add_handler(CallbackQueryHandler(toggle_can_edit, pattern=r"^set:agents:edit:\w+$"))
    application.add_handler(CallbackQueryHandler(toggle_can_push, pattern=r"^set:agents:push:\w+$"))
    application.add_handler(CallbackQueryHandler(send_accounts_list, pattern=r"^set:accounts_list$"))
    application.add_handler(CallbackQueryHandler(prompt_account_note, pattern=r"^set:accnote:\w+:.+$"))
    application.add_handler(CallbackQueryHandler(show_custom_api, pattern=r"^set:customapi$"))
    application.add_handler(
        CallbackQueryHandler(cycle_custom_api_auth, pattern=r"^set:customapi:auth:[\w:]+$")
    )
    application.add_handler(
        CallbackQueryHandler(cycle_custom_api_format, pattern=r"^set:customapi:format:[\w:]+$")
    )
    application.add_handler(
        CallbackQueryHandler(prompt_custom_api_field, pattern=r"^set:customapi:(name|url|model):[\w:]+$")
    )
    application.add_handler(
        CallbackQueryHandler(clear_custom_api_slot, pattern=r"^set:customapi:clear:[\w:]+$")
    )
    application.add_handler(CallbackQueryHandler(show_custom_api_account, pattern=r"^set:customapi:[\w:]+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_api_text), group=9)
    application.add_handler(CallbackQueryHandler(show_provider_key, pattern=r"^set:key:\w+$"))
    application.add_handler(CallbackQueryHandler(prompt_set_provider_key, pattern=r"^set:key_set:\w+$"))
    application.add_handler(CallbackQueryHandler(prompt_set_provider_model, pattern=r"^set:model_set:\w+$"))
    application.add_handler(CallbackQueryHandler(prompt_clear_provider_key, pattern=r"^set:key_clear:\w+$"))
    application.add_handler(CallbackQueryHandler(clear_provider_key, pattern=r"^set:key_clear_yes:\w+$"))
    application.add_handler(CallbackQueryHandler(prompt_add_extra_account, pattern=r"^set:key_add:\w+$"))
    application.add_handler(
        CallbackQueryHandler(prompt_delete_extra_account, pattern=r"^set:key_del:\w+:\d+$")
    )
    application.add_handler(CallbackQueryHandler(delete_extra_account, pattern=r"^set:key_del_yes:\w+:\d+$"))
    application.add_handler(CallbackQueryHandler(show_history_projects, pattern=r"^menu:history$"))
    application.add_handler(CallbackQueryHandler(show_history_projects_page, pattern=r"^hist:page:\d+$"))
    application.add_handler(CallbackQueryHandler(show_history_for_project, pattern=r"^hist:proj:\d+(:\d+)?$"))
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
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_proxies_manual_text), group=5
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_provider_model_text), group=6
    )
