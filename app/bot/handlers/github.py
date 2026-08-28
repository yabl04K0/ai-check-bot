"""🐙 GitHub — список репо, видимость, issues. Удаление репо НЕДОСТУПНО
через бота ни в UI, ни в коде (см. app/github_integration)."""

from __future__ import annotations

import hashlib

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.keyboards import confirm_row, nav_row, paginate_rows
from app.db.models import ProviderName
from app.db.session import get_session
from app.github_integration.client import GitHubClient, GitHubError
from app.github_integration.rotation import check_token_age
from app.github_integration.token_store import (
    clear_token_override,
    get_token_override,
    resolve_github_token,
    set_token_override,
)
from app.logging_setup import log_action
from app.providers.cursor import CursorProvider
from app.providers.registry import ProviderRegistry

NO_TOKEN_TEXT = (
    "🐙 GitHub-токен не задан.\n\n"
    "Задай его кнопкой «🔑 Задать/обновить токен» ниже, или GITHUB_TOKEN "
    "в .env (нужен рестарт бота)."
)

REPOS_PAGE_SIZE = 8


def _repo_key(full_name: str) -> str:
    """Стабильный короткий ключ вместо позиционного индекса в списке —
    индекс "плывёт", если GitHub вернёт репозитории в другом порядке
    между рендером списка и тапом по кнопке (пересортировка на их
    стороне, гонка двух открытых экранов и т.п.), и тогда callback бьёт
    не по тому репо. Не сам full_name — не помещается в 64-байтный лимит
    callback_data Telegram на длинных owner/repo."""
    return hashlib.sha256(full_name.encode()).hexdigest()[:12]


def _invalidate_client_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.application.bot_data.pop("github_client", None)
    context.application.bot_data.pop("github_client_token", None)


def _get_client(context: ContextTypes.DEFAULT_TYPE) -> GitHubClient | None:
    settings = context.application.bot_data["settings"]
    token = resolve_github_token(settings)
    if not token:
        return None
    # Кэш инвалидируется по совпадению токена, а не только по наличию ключа
    # — иначе смена токена через бота (см. receive_token_text) не подхватится
    # без рестарта, пока не истечёт что-то ещё, чего тут нет.
    if context.application.bot_data.get("github_client_token") != token:
        context.application.bot_data["github_client"] = GitHubClient(token)
        context.application.bot_data["github_client_token"] = token
    return context.application.bot_data["github_client"]


async def show_github_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    client = _get_client(context)
    if client is None:
        await query.edit_message_text(NO_TOKEN_TEXT, reply_markup=_no_token_menu())
        return

    try:
        repos = client.list_repos()
    except GitHubError as exc:
        await query.edit_message_text(
            f"Ошибка GitHub API: {exc}", reply_markup=InlineKeyboardMarkup([nav_row()])
        )
        return

    context.user_data["gh_repos"] = {_repo_key(r.full_name): r for r in repos}
    repo_rows = []
    for r in repos:
        label = f"{'🔒' if r.private else '🌐'} {r.full_name}"
        repo_rows.append([InlineKeyboardButton(label, callback_data=f"gh:repo:{_repo_key(r.full_name)}")])
    # Пагинация по 8, как остальные крупные списки бота (проекты, находки
    # реестра, issues) — раньше это был единственный такой список без неё
    # (см. аудит меню), при заметном числе репо экран превращался в
    # простыню кнопок без ограничения.
    page_rows, total_pages = paginate_rows(repo_rows, page, nav_prefix="gh:page", per_page=REPOS_PAGE_SIZE)
    page_rows.append([InlineKeyboardButton("⚡ Закрыть все публичные", callback_data="gh:close_public")])
    page_rows.append([InlineKeyboardButton("🔑 Токен", callback_data="gh:token")])
    page_rows.append(nav_row())

    settings = context.application.bot_data["settings"]
    with get_session() as session:
        age = check_token_age(session, resolve_github_token(settings))
        session.commit()
    header = "📋 Репозитории:" if repos else "📋 Репозитории не найдены — проверь права токена."
    page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""
    header = f"{header}{page_note}"
    if age.needs_rotation_warning:
        header = f"⚠️ Токену {age.days_since} дн. — пора переиздать (см. 🔑 Токен)\n\n{header}"

    await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(page_rows))


async def show_github_menu_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await show_github_menu(update, context, page=page)


def _token_menu(*, has_override: bool, back_target: str = "menu:github") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🔑 Задать/обновить токен", callback_data="gh:token_set")]]
    if has_override:
        # token_clear_ask, не token_clear напрямую — один случайный тап
        # раньше полностью снимал переопределённый токен без подтверждения
        # (см. аудит меню; аналогичные очистки в других разделах бота уже
        # требуют confirm_row).
        rows.append(
            [InlineKeyboardButton("🗑 Убрать (вернуться к .env)", callback_data="gh:token_clear_ask")]
        )
    rows.append(nav_row(back_target))
    return InlineKeyboardMarkup(rows)


def _no_token_menu() -> InlineKeyboardMarkup:
    # back_target="menu:main", не "menu:github" — этот экран показывается
    # ИЗ show_github_menu/close_public, то есть это и есть menu:github:
    # "Назад" на него же было бы тупиковой петлёй без единого выхода
    # инлайн-кнопками (см. аудит меню).
    return _token_menu(has_override=False, back_target="menu:main")


async def _render_token_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Общий рендер экрана токена — без query.answer(), потому что вызывающие
    хендлеры (show_token_status и clear_token) сами уже ответили на callback
    query своим текстом, а Telegram не даёт отвечать на один callback дважды."""
    query = update.callback_query
    settings = context.application.bot_data["settings"]
    has_override = get_token_override() is not None
    token = resolve_github_token(settings)
    markup = _token_menu(has_override=has_override)

    if not token:
        await query.edit_message_text(NO_TOKEN_TEXT, reply_markup=markup)
        return

    with get_session() as session:
        age = check_token_age(session, token)
        session.commit()

    if age.needs_rotation_warning:
        status_line = f"⚠️ Активен уже {age.days_since} дн. — пора переиздать"
    else:
        status_line = f"✅ Активен, {age.days_since} дн. с момента, как бот увидел этот токен"

    source = "бот (переопределяет .env)" if has_override else ".env"
    text = (
        f"🔑 Токен (источник: {source})\n{status_line}\n\n"
        "Переиздать: создай новый fine-grained PAT в GitHub (Settings → "
        "Developer settings) и пришли его через «Задать/обновить токен» — "
        "применяется сразу, рестарт бота не нужен.\n\n"
        "(Дата создания токена не отдаётся GitHub API для fine-grained PAT — "
        "отсчёт идёт с момента, когда бот впервые увидел этот токен, это "
        "оценка, не точная дата выпуска.)"
    )
    await query.edit_message_text(text, reply_markup=markup)


async def show_token_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _render_token_status(update, context)


async def prompt_set_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "github_token"
    await query.edit_message_text(
        "🔑 Пришли новый GitHub-токен следующим сообщением.\n\n"
        "Fine-grained PAT: Contents (rw) + Administration (только смена "
        "видимости), БЕЗ delete_repo — см. README.\n\n"
        "Сообщение с токеном будет сразу удалено ботом из чата после сохранения.",
        reply_markup=InlineKeyboardMarkup([nav_row("menu:github")]),
    )


async def receive_token_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "github_token":
        return
    context.user_data["awaiting"] = None

    settings = context.application.bot_data["settings"]
    # admin_tg_id может быть НЕ задан вовсе — официально поддерживаемый
    # "открытый режим" (см. app.bot.access_control.is_authorized), не
    # гипотетический сбой. Раньше проверка срабатывала и в этом режиме,
    # молча отказывая ВСЕМ: пользователь думал, что токен принят (бот же
    # попросил его прислать), а сообщение с токеном даже не удалялось
    # (return был раньше update.message.delete() ниже) — см. аудит меню.
    if settings.admin_tg_id and update.effective_user.id != settings.admin_tg_id:
        return  # флаг мог остаться от другого юзера, если был сброшен странно

    token = update.message.text.strip()

    # Токен в открытом чате — секрет, который не должен оставаться в
    # истории переписки. Бот может удалять входящие сообщения в приватных
    # чатах (Telegram Bot API), поэтому чистим сообщение сразу, независимо
    # от того, валиден ли текст как токен.
    try:
        await update.message.delete()
    except TelegramError:
        pass  # не критично для сохранения токена, просто не смогли подчистить чат

    if not token or any(ch.isspace() for ch in token):
        await context.bot.send_message(
            update.effective_chat.id,
            "⚠️ Похоже на не тот текст — токен не сохранён. Открой 🐙 GitHub → 🔑 Токен ещё раз.",
        )
        return

    set_token_override(token)
    _invalidate_client_cache(context)
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    cursor_provider = registry.get(ProviderName.CURSOR)
    if isinstance(cursor_provider, CursorProvider):
        cursor_provider.update_github_token(token)

    log_action(str(update.effective_user.id), "github_token_set_via_bot", "")
    await context.bot.send_message(
        update.effective_chat.id,
        "✅ GitHub-токен сохранён и уже используется — рестарт бота не нужен.",
        reply_markup=InlineKeyboardMarkup([nav_row("menu:github")]),
    )


async def prompt_clear_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🗑 Убрать переопределённый токен и вернуться к GITHUB_TOKEN из .env?",
        reply_markup=InlineKeyboardMarkup([confirm_row("gh:token_clear", "gh:token")]),
    )


async def clear_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    clear_token_override()
    _invalidate_client_cache(context)
    settings = context.application.bot_data["settings"]
    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    cursor_provider = registry.get(ProviderName.CURSOR)
    if isinstance(cursor_provider, CursorProvider):
        cursor_provider.update_github_token(settings.github_token)

    log_action(str(update.effective_user.id), "github_token_override_cleared", "")
    await query.answer("Убрано")
    await _render_token_status(update, context)


def _repo_menu(key: str, private: bool) -> InlineKeyboardMarkup:
    toggle_label = "🌐 Открыть" if private else "🔒 Закрыть"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_label, callback_data=f"gh:toggle:{key}")],
            [InlineKeyboardButton("📝 Открытые issues", callback_data=f"gh:issues:{key}")],
            nav_row("menu:github"),
        ]
    )


async def show_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    key = query.data.split(":")[-1]
    repos = context.user_data.get("gh_repos", {})
    repo = repos.get(key)
    if repo is None:
        await query.edit_message_text(
            "Список устарел, открой 🐙 GitHub заново.", reply_markup=InlineKeyboardMarkup([nav_row()])
        )
        return
    text = f"{repo.full_name}\n{'приватный' if repo.private else 'публичный'} · issues: {repo.open_issues}"
    await query.edit_message_text(text, reply_markup=_repo_menu(key, repo.private))


async def toggle_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":")[-1]
    repos = context.user_data.get("gh_repos", {})
    repo = repos.get(key)
    if repo is None:
        await query.answer("Список устарел.", show_alert=True)
        return
    client = _get_client(context)
    try:
        updated = client.set_visibility(repo.full_name, private=not repo.private)
    except GitHubError as exc:
        # Telegram допускает ограниченную длину текста в answer() — длинное
        # исключение (GitHubError оборачивает исходную httpx/API-ошибку)
        # раньше могло уронить сам вызов answer(), оставляя спиннер на
        # кнопке зависшим без единой видимой причины (см. аудит меню).
        await query.answer(str(exc)[:200], show_alert=True)
        return
    repos[key] = updated
    log_action(
        str(update.effective_user.id),
        "github_set_visibility",
        f"{updated.full_name} private={updated.private}",
    )
    await query.answer("Ок")
    visibility = "приватный" if updated.private else "публичный"
    text = f"{updated.full_name}\n{visibility} · issues: {updated.open_issues}"
    await query.edit_message_text(text, reply_markup=_repo_menu(key, updated.private))


ISSUES_PAGE_SIZE = 8


async def show_issues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # answer() один раз за callback (Telegram отвергает повторный) — сперва
    # проверяем staleness, чтобы решить, каким именно текстом ответить.
    query = update.callback_query
    parts = query.data.split(":")
    key = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 0
    repos = context.user_data.get("gh_repos", {})
    repo = repos.get(key)
    if repo is None:
        await query.answer("Список устарел.", show_alert=True)
        return
    await query.answer()
    client = _get_client(context)
    # nav_row(f"gh:repo:{key}"), не _repo_menu(key, ...) — issues это экран
    # ОДНИМ уровнем глубже карточки репо, а _repo_menu вела "Назад" сразу
    # на menu:github (полный список репо), перепрыгивая через карточку,
    # с которой пользователь реально сюда зашёл (см. аудит меню).
    back_row = nav_row(f"gh:repo:{key}")
    try:
        issues = client.list_issues(repo.full_name)
    except GitHubError as exc:
        await query.edit_message_text(f"Ошибка: {exc}", reply_markup=InlineKeyboardMarkup([back_row]))
        return
    if not issues:
        text = f"{repo.full_name}: открытых issues нет."
        markup = InlineKeyboardMarkup([back_row])
    else:
        total_pages = max(1, -(-len(issues) // ISSUES_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        page_issues = issues[page * ISSUES_PAGE_SIZE : page * ISSUES_PAGE_SIZE + ISSUES_PAGE_SIZE]
        page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""
        lines = [f"{repo.full_name} — открытые issues ({len(issues)}){page_note}:"]
        for issue in page_issues:
            lines.append(f"#{issue['number']} {issue['title']}")
        text = "\n".join(lines)
        rows = []
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("◀️", callback_data=f"gh:issues:{key}:{page - 1}"))
            nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("▶️", callback_data=f"gh:issues:{key}:{page + 1}"))
            rows.append(nav)
        rows.append(back_row)
        markup = InlineKeyboardMarkup(rows)
    await query.edit_message_text(text, reply_markup=markup)


async def confirm_close_public(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    markup = InlineKeyboardMarkup(
        [confirm_row("gh:close_public_confirm", "menu:github", yes_label="✅ Закрыть все публичные")]
    )
    await query.edit_message_text("Точно закрыть все публичные репозитории?", reply_markup=markup)


async def close_public(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    client = _get_client(context)
    if client is None:
        await query.edit_message_text(NO_TOKEN_TEXT, reply_markup=_no_token_menu())
        return
    try:
        result = client.close_all_public()
    except GitHubError as exc:
        await query.edit_message_text(
            f"Ошибка: {exc}", reply_markup=InlineKeyboardMarkup([nav_row("menu:github")])
        )
        return

    log_action(
        str(update.effective_user.id),
        "github_close_all_public",
        f"closed={result.closed} failed={[name for name, _ in result.failed]}",
    )

    if not result.closed and not result.failed:
        text = "Публичных репозиториев не было."
    else:
        lines = [f"✅ Закрыто: {len(result.closed)}"] + result.closed
        if result.failed:
            lines.append(f"\n❌ Не удалось: {len(result.failed)}")
            lines.extend(f"{name}: {error}" for name, error in result.failed)
        text = "\n".join(lines)
    await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup([nav_row("menu:github")]))


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_github_menu, pattern=r"^menu:github$"))
    application.add_handler(CallbackQueryHandler(show_github_menu_page, pattern=r"^gh:page:\d+$"))
    application.add_handler(CallbackQueryHandler(show_token_status, pattern=r"^gh:token$"))
    application.add_handler(CallbackQueryHandler(prompt_set_token, pattern=r"^gh:token_set$"))
    application.add_handler(CallbackQueryHandler(prompt_clear_token, pattern=r"^gh:token_clear_ask$"))
    application.add_handler(CallbackQueryHandler(clear_token, pattern=r"^gh:token_clear$"))
    application.add_handler(CallbackQueryHandler(show_repo, pattern=r"^gh:repo:[0-9a-f]{12}$"))
    application.add_handler(CallbackQueryHandler(toggle_visibility, pattern=r"^gh:toggle:[0-9a-f]{12}$"))
    application.add_handler(
        CallbackQueryHandler(show_issues, pattern=r"^gh:issues:[0-9a-f]{12}(:\d+)?$")
    )
    application.add_handler(CallbackQueryHandler(confirm_close_public, pattern=r"^gh:close_public$"))
    application.add_handler(CallbackQueryHandler(close_public, pattern=r"^gh:close_public_confirm$"))
    # Отдельная группа от settings_admin.on_text (у "awaiting" общий
    # user_data, но PTB выполняет максимум один хендлер на группу за апдейт
    # — два текстовых хендлера в одной группе конкурировали бы за один и
    # тот же текст).
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token_text), group=3
    )
