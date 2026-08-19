"""📁 Проекты — список, добавление, настройки проекта."""

from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.keyboards import back_button
from app.db.models import Project
from app.db.session import get_session

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
    rows = [
        [InlineKeyboardButton(auto_label, callback_data=f"proj:toggle_auto:{project.id}")],
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
            f"local_path: {project.local_path or '(не задан)'}"
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
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "add_project"
    await query.edit_message_text(ADD_PROJECT_PROMPT, reply_markup=back_button("menu:projects"))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "add_project":
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

    with get_session() as session:
        session.add(Project(name=name, repo_full_name=repo_full_name, local_path=local_path))
        session.commit()

    context.user_data["awaiting"] = None
    await update.message.reply_text(f"✅ Добавлено: {name} ({repo_full_name})")


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_projects, pattern=r"^menu:projects$"))
    application.add_handler(CallbackQueryHandler(prompt_add_project, pattern=r"^proj:add$"))
    application.add_handler(CallbackQueryHandler(manage_project, pattern=r"^proj:manage:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_autocheck, pattern=r"^proj:toggle_auto:\d+$"))
    application.add_handler(CallbackQueryHandler(delete_project, pattern=r"^proj:del:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=1)
