"""📁 Проекты — список, добавление, настройки проекта."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.keyboards import back_button
from app.db.models import Project
from app.db.session import get_session
from app.github_integration.client import GitHubClient, GitHubError
from app.logging_setup import log_action
from app.tasks.local_repos import detect_repo_full_name, discover_local_repos
from app.tasks.patch_apply import commit_all, has_uncommitted_changes
from app.tasks.project_context import local_path as project_local_path

ADD_PROJECT_PROMPT = (
    "Отправь одной строкой: `Имя проекта; owner/repo` "
    "(например: `AutoPost; myuser/autopost`).\n"
    "Локальный путь для чекаута можно добавить третьим полем через `;`."
)


def _list_projects() -> list[Project]:
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
        return list(projects)


def _management_menu(projects: list[Project]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{'🤖 ' if p.is_self else ''}{p.name}", callback_data=f"proj:manage:{p.id}")]
        for p in projects
    ]
    rows.append([InlineKeyboardButton("➕ Добавить проект", callback_data="proj:add")])
    rows.append([back_button()])
    return InlineKeyboardMarkup(rows)


async def show_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    projects = _list_projects()
    text = "📁 Проекты" if projects else "📁 Проекты — пока пусто, добавь первый."
    await query.edit_message_text(text, reply_markup=_management_menu(projects))


def _project_settings_menu(project: Project) -> InlineKeyboardMarkup:
    auto_label = "🔔 Авточек: вкл" if project.autocheck_enabled else "🔔 Авточек: выкл"
    self_label = "🤖 Self-check: вкл" if project.is_self else "🤖 Self-check: выкл"
    rows = [
        [InlineKeyboardButton(auto_label, callback_data=f"proj:toggle_auto:{project.id}")],
        [InlineKeyboardButton(self_label, callback_data=f"proj:toggle_self:{project.id}")],
        [InlineKeyboardButton("📤 Запушить (без ИИ)", callback_data=f"proj:push:{project.id}")],
        [InlineKeyboardButton("📜 Реестр багов", callback_data=f"reg:tab:{project.id}:open")],
        [InlineKeyboardButton("🕘 История", callback_data=f"hist:proj:{project.id}")],
        [InlineKeyboardButton("🗑️ Убрать из списка", callback_data=f"proj:del:{project.id}")],
        [back_button("menu:projects")],
    ]
    return InlineKeyboardMarkup(rows)


async def manage_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=back_button("menu:projects"))
            return
        text = (
            f"⚙️ {project.name}\n"
            f"repo: {project.repo_full_name}\n"
            f"local_path: {project.local_path or '(не задан)'}\n"
            f"self-check: {'да' if project.is_self else 'нет'}"
        )
        markup = _project_settings_menu(project)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_autocheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.answer("Проект не найден.")
            return
        project.autocheck_enabled = not project.autocheck_enabled
        session.commit()
        text = f"⚙️ {project.name}\nrepo: {project.repo_full_name}"
        markup = _project_settings_menu(project)
    await query.answer("Ок")
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_self_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """is_self защищает от автопуша (см. commit_yes в bot/handlers/check.py)
    и раньше нигде не выставлялся — self-check не срабатывал ни разу,
    даже когда проект действительно был репозиторием самого бота."""
    query = update.callback_query
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.answer("Проект не найден.")
            return
        project.is_self = not project.is_self
        session.commit()
        text = f"⚙️ {project.name}\nrepo: {project.repo_full_name}"
        markup = _project_settings_menu(project)
    await query.answer("Ок")
    await query.edit_message_text(text, reply_markup=markup)


def _manual_push_blocking(project_id: int, github_token: str | None) -> str:
    """Пуш БЕЗ участия ИИ — коммитит что есть на диске (если что-то
    незакоммичено) и пушит существующие коммиты. Это тот самый "запушь
    вручную через 🐙 GitHub", который self-check раньше только советовал
    текстом (см. app/bot/handlers/check.py::_apply_and_commit_blocking) —
    теперь у него есть кнопка, и она работает для self-check тоже: это
    человек нажимает её сам, автопуш ИИ тут ни при чём."""
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return "Проект не найден."
        name = project.name
        repo_full_name = project.repo_full_name

    path = project_local_path(project)
    if path is None:
        return f"⚠️ У {name} не задан local_path (или путь недоступен) — некуда пушить."
    if not github_token:
        return "⚠️ GITHUB_TOKEN не задан в .env — нечем пушить."

    if has_uncommitted_changes(path):
        ok, detail = commit_all(path, "Ручной пуш через бота (без ИИ)")
        if not ok:
            return f"❌ Есть незакоммиченные изменения, но commit не удался:\n{detail[:1500]}"

    try:
        client = GitHubClient(github_token)
        push_result = client.push_commit(path)
    except GitHubError as exc:
        return f"❌ Push не удался: {exc}"

    log_action(str(project_id), "manual_push", repo_full_name)
    return f"✅ Запушено вручную (без ИИ): {name}\n{push_result or 'ok'}"


async def manual_push(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    settings = context.application.bot_data["settings"]
    await query.edit_message_text("⏳ Пушу без участия ИИ…")
    text = await asyncio.to_thread(_manual_push_blocking, project_id, settings.github_token)
    await query.edit_message_text(text, reply_markup=back_button(f"proj:manage:{project_id}"))


async def delete_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is not None:
            session.delete(project)
            session.commit()
    await query.answer("Убрано")
    projects = _list_projects()
    await query.edit_message_text("📁 Проекты", reply_markup=_management_menu(projects))


async def prompt_add_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """LOCAL_REPOS_ROOT не задан в .env — ведём себя как раньше (сразу
    ручной ввод), чтобы ничего не менялось для тех, кто эту фичу не
    настраивал. Задан — предлагаем выбор: удобный список кнопкой или
    ручной ввод."""
    query = update.callback_query
    await query.answer()
    settings = context.application.bot_data["settings"]
    if settings.local_repos_root is None:
        context.user_data["awaiting"] = "add_project"
        await query.edit_message_text(ADD_PROJECT_PROMPT, reply_markup=back_button("menu:projects"))
        return

    rows = [
        [InlineKeyboardButton("📂 Выбрать локальный репозиторий", callback_data="proj:add:browse")],
        [InlineKeyboardButton("✍️ Ввести вручную", callback_data="proj:add:manual")],
        [back_button("menu:projects")],
    ]
    await query.edit_message_text("➕ Добавить проект — как?", reply_markup=InlineKeyboardMarkup(rows))


async def prompt_add_project_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "add_project"
    await query.edit_message_text(ADD_PROJECT_PROMPT, reply_markup=back_button("menu:projects"))


async def browse_local_repos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    settings = context.application.bot_data["settings"]
    root = settings.local_repos_root
    if root is None:
        await query.edit_message_text(
            "LOCAL_REPOS_ROOT не задан в .env.", reply_markup=back_button("menu:projects")
        )
        return

    repos = discover_local_repos(root)
    if not repos:
        await query.edit_message_text(
            f"Не нашёл git-репозиториев в {root}.", reply_markup=back_button("menu:projects")
        )
        return

    with get_session() as session:
        already_added = {
            p.local_path for p in session.scalars(select(Project)).all() if p.local_path
        }

    context.user_data["local_repo_candidates"] = [str(p) for p in repos]
    rows = []
    for i, repo_path in enumerate(repos):
        mark = " ✅" if str(repo_path) in already_added else ""
        rows.append([InlineKeyboardButton(f"{repo_path.name}{mark}", callback_data=f"proj:add:pick:{i}")])
    rows.append([back_button("menu:projects")])
    await query.edit_message_text(f"📂 Репозитории в {root}:", reply_markup=InlineKeyboardMarkup(rows))


async def pick_local_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[-1])
    candidates = context.user_data.get("local_repo_candidates", [])
    if index >= len(candidates):
        await query.edit_message_text(
            "Список устарел, открой добавление проекта заново.", reply_markup=back_button("menu:projects")
        )
        return

    path = Path(candidates[index])
    name = path.name
    repo_full_name = detect_repo_full_name(path)

    if repo_full_name is None:
        context.user_data["pending_local_project"] = {"name": name, "local_path": str(path)}
        context.user_data["awaiting"] = "add_project_repo_name"
        await query.edit_message_text(
            f"Не смог определить owner/repo из git remote для {name} ({path}).\n"
            "Отправь текстом `owner/repo`:",
            parse_mode="Markdown",
        )
        return

    with get_session() as session:
        existing = session.scalar(select(Project).where(Project.repo_full_name == repo_full_name))
        if existing is not None:
            await query.edit_message_text(
                f"⚠️ Проект с repo {repo_full_name} уже есть в списке: {existing.name}.",
                reply_markup=back_button("menu:projects"),
            )
            return
        session.add(Project(name=name, repo_full_name=repo_full_name, local_path=str(path)))
        session.commit()

    await query.edit_message_text(
        f"✅ Добавлено: {name} ({repo_full_name})\nlocal_path: {path}",
        reply_markup=back_button("menu:projects"),
    )


def _create_project_or_report_duplicate(name: str, repo_full_name: str, local_path: str | None) -> str:
    with get_session() as session:
        existing = session.scalar(select(Project).where(Project.repo_full_name == repo_full_name))
        if existing is not None:
            return f"⚠️ Проект с repo {repo_full_name} уже есть в списке: {existing.name}."
        session.add(Project(name=name, repo_full_name=repo_full_name, local_path=local_path))
        session.commit()
    return f"✅ Добавлено: {name} ({repo_full_name})"


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")

    if awaiting == "add_project_repo_name":
        pending = context.user_data.pop("pending_local_project", None)
        context.user_data["awaiting"] = None
        if pending is None:
            return
        repo_full_name = update.message.text.strip()
        if "/" not in repo_full_name:
            await update.message.reply_text("Формат: `owner/repo`.", parse_mode="Markdown")
            return
        text = _create_project_or_report_duplicate(
            pending["name"], repo_full_name, pending["local_path"]
        )
        await update.message.reply_text(text)
        return

    if awaiting != "add_project":
        return
    raw = update.message.text.strip()
    parts = [p.strip() for p in raw.split(";")]
    if len(parts) < 2:
        await update.message.reply_text(
            "Не понял формат. " + ADD_PROJECT_PROMPT, parse_mode="Markdown"
        )
        return
    name, repo_full_name = parts[0], parts[1]
    local_path = parts[2] if len(parts) > 2 else None

    context.user_data["awaiting"] = None
    text = _create_project_or_report_duplicate(name, repo_full_name, local_path)
    await update.message.reply_text(text)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_projects, pattern=r"^menu:projects$"))
    application.add_handler(CallbackQueryHandler(prompt_add_project, pattern=r"^proj:add$"))
    application.add_handler(CallbackQueryHandler(prompt_add_project_manual, pattern=r"^proj:add:manual$"))
    application.add_handler(CallbackQueryHandler(browse_local_repos, pattern=r"^proj:add:browse$"))
    application.add_handler(CallbackQueryHandler(pick_local_repo, pattern=r"^proj:add:pick:\d+$"))
    application.add_handler(CallbackQueryHandler(manage_project, pattern=r"^proj:manage:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_autocheck, pattern=r"^proj:toggle_auto:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_self_check, pattern=r"^proj:toggle_self:\d+$"))
    application.add_handler(CallbackQueryHandler(manual_push, pattern=r"^proj:push:\d+$"))
    application.add_handler(CallbackQueryHandler(delete_project, pattern=r"^proj:del:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=1)
