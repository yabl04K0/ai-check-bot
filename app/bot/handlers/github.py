"""🐙 GitHub — список репо, видимость, issues. Удаление репо НЕДОСТУПНО
через бота ни в UI, ни в коде (см. app/github_integration)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.bot.keyboards import back_button
from app.github_integration.client import GitHubClient, GitHubError
from app.logging_setup import log_action


def _get_client(context: ContextTypes.DEFAULT_TYPE) -> GitHubClient | None:
    settings = context.application.bot_data["settings"]
    if not settings.github_token:
        return None
    if "github_client" not in context.application.bot_data:
        context.application.bot_data["github_client"] = GitHubClient(settings.github_token)
    return context.application.bot_data["github_client"]


async def show_github_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    client = _get_client(context)
    if client is None:
        await query.edit_message_text(
            "🐙 GitHub-токен не задан (GITHUB_TOKEN в .env).", reply_markup=back_button()
        )
        return

    try:
        repos = client.list_repos()
    except GitHubError as exc:
        await query.edit_message_text(f"Ошибка GitHub API: {exc}", reply_markup=back_button())
        return

    context.user_data["gh_repos"] = repos
    rows = [
        [InlineKeyboardButton(f"{'🔒' if r.private else '🌐'} {r.full_name}", callback_data=f"gh:repo:{i}")]
        for i, r in enumerate(repos)
    ]
    rows.append([InlineKeyboardButton("⚡ Закрыть все публичные", callback_data="gh:close_public")])
    rows.append([back_button()])
    await query.edit_message_text("📋 Репозитории:", reply_markup=InlineKeyboardMarkup(rows))


def _repo_menu(index: int, private: bool) -> InlineKeyboardMarkup:
    toggle_label = "🌐 Открыть" if private else "🔒 Закрыть"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_label, callback_data=f"gh:toggle:{index}")],
            [InlineKeyboardButton("📝 Открытые issues", callback_data=f"gh:issues:{index}")],
            [back_button("menu:github")],
        ]
    )


async def show_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[-1])
    repos = context.user_data.get("gh_repos", [])
    if index >= len(repos):
        await query.edit_message_text("Список устарел, открой 🐙 GitHub заново.", reply_markup=back_button())
        return
    repo = repos[index]
    text = f"{repo.full_name}\n{'приватный' if repo.private else 'публичный'} · issues: {repo.open_issues}"
    await query.edit_message_text(text, reply_markup=_repo_menu(index, repo.private))


async def toggle_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    index = int(query.data.split(":")[-1])
    repos = context.user_data.get("gh_repos", [])
    if index >= len(repos):
        await query.answer("Список устарел.", show_alert=True)
        return
    client = _get_client(context)
    repo = repos[index]
    try:
        updated = client.set_visibility(repo.full_name, private=not repo.private)
    except GitHubError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    repos[index] = updated
    log_action(str(update.effective_user.id), "github_set_visibility", f"{updated.full_name} private={updated.private}")
    await query.answer("Ок")
    text = f"{updated.full_name}\n{'приватный' if updated.private else 'публичный'} · issues: {updated.open_issues}"
    await query.edit_message_text(text, reply_markup=_repo_menu(index, updated.private))


async def show_issues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[-1])
    repos = context.user_data.get("gh_repos", [])
    if index >= len(repos):
        await query.answer("Список устарел.", show_alert=True)
        return
    client = _get_client(context)
    repo = repos[index]
    try:
        issues = client.list_issues(repo.full_name)
    except GitHubError as exc:
        await query.edit_message_text(f"Ошибка: {exc}", reply_markup=_repo_menu(index, repo.private))
        return
    if not issues:
        text = f"{repo.full_name}: открытых issues нет."
    else:
        lines = [f"{repo.full_name} — открытые issues:"]
        for issue in issues[:20]:
            lines.append(f"#{issue['number']} {issue['title']}")
        text = "\n".join(lines)
    await query.edit_message_text(text, reply_markup=_repo_menu(index, repo.private))


async def confirm_close_public(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, закрыть все публичные", callback_data="gh:close_public_confirm")],
            [back_button("menu:github")],
        ]
    )
    await query.edit_message_text("Точно закрыть все публичные репозитории?", reply_markup=markup)


async def close_public(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    client = _get_client(context)
    if client is None:
        await query.edit_message_text("GitHub-токен не задан.", reply_markup=back_button())
        return
    try:
        closed = client.close_all_public()
    except GitHubError as exc:
        await query.edit_message_text(f"Ошибка: {exc}", reply_markup=back_button("menu:github"))
        return
    log_action(str(update.effective_user.id), "github_close_all_public", ", ".join(closed))
    text = f"✅ Закрыто: {len(closed)}\n" + "\n".join(closed) if closed else "Публичных репозиториев не было."
    await query.edit_message_text(text, reply_markup=back_button("menu:github"))


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_github_menu, pattern=r"^menu:github$"))
    application.add_handler(CallbackQueryHandler(show_repo, pattern=r"^gh:repo:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_visibility, pattern=r"^gh:toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(show_issues, pattern=r"^gh:issues:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_close_public, pattern=r"^gh:close_public$"))
    application.add_handler(CallbackQueryHandler(close_public, pattern=r"^gh:close_public_confirm$"))
