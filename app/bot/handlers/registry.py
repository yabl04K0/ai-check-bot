"""📜 Реестр — 3 вкладки (Открыто/Отложено/Never), источник — файлы в репо."""

from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.bot.keyboards import PAGE_SIZE, nav_row, paginate_rows, registry_tabs
from app.db.models import Finding, FindingStatus, Project
from app.db.session import get_session
from app.registry_store.sync import sync_project_findings
from app.tasks.project_context import local_path as project_local_path
from app.tasks.types import SEVERITY_EMOJI

STATUS_TITLES = {"open": "🔴 ОТКРЫТО", "later": "🟡 ОТЛОЖЕНО", "never": "⚫ NEVER"}


async def show_registry_projects(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    if not projects:
        await query.edit_message_text("Проектов пока нет.", reply_markup=InlineKeyboardMarkup([nav_row()]))
        return
    rows = [[InlineKeyboardButton(p.name, callback_data=f"reg:pick:{p.id}")] for p in projects]
    page_rows, total_pages = paginate_rows(rows, page, nav_prefix="reg:projpage")
    page_rows.append(nav_row())
    title = "Какой проект?" + (f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else "")
    await query.edit_message_text(title, reply_markup=InlineKeyboardMarkup(page_rows))


async def show_registry_projects_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await show_registry_projects(update, context, page=page)


async def pick_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        name = project.name if project else "?"
    await query.edit_message_text(f"📜 Реестр — {name}", reply_markup=registry_tabs(project_id))


async def show_tab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Читает из SQLite-кэша (Finding), не из .md напрямую — источник
    правды по-прежнему файлы в репо, но UI ходит в кэш и синкает его перед
    показом, если есть локальный чекаут (см. app.registry_store.sync)."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    project_id = int(parts[2])
    status = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 0
    target_status = FindingStatus(status)

    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text(
                "Проект не найден.", reply_markup=InlineKeyboardMarkup([nav_row("menu:registry")])
            )
            return
        name = project.name
        has_local = project_local_path(project) is not None
        if has_local:
            sync_project_findings(session, project)
            session.commit()

        findings = session.scalars(
            select(Finding)
            .where(Finding.project_id == project_id, Finding.status == target_status)
            .order_by(Finding.updated_at.desc())
        ).all()
        session.expunge_all()

    stale_note = "" if has_local else " (нет local_path — показан последний известный кэш)"
    total = len(findings)
    total_pages = max(1, -(-total // PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    page_findings = findings[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]
    page_note = f" · стр. {page + 1}/{total_pages}" if total_pages > 1 else ""

    if not findings:
        text = f"📜 {name} — {STATUS_TITLES[status]} (0){stale_note}"
    else:
        lines = [f"📜 {name} — {STATUS_TITLES[status]} ({total}){stale_note}{page_note}"]
        for f in page_findings:
            emoji = SEVERITY_EMOJI.get(f.severity, "") if status == "open" else ""
            extra = f" · attempts={f.attempts}" if status == "open" else f" · {f.reason or ''}"
            lines.append(f"{emoji} {f.file_symbol}{extra}")
        text = "\n".join(lines)

    markup = registry_tabs(project_id)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"reg:tab:{project_id}:{status}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"reg:tab:{project_id}:{status}:{page + 1}"))
        markup = InlineKeyboardMarkup([*markup.inline_keyboard, nav])

    await query.edit_message_text(text, reply_markup=markup)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_registry_projects, pattern=r"^menu:registry$"))
    application.add_handler(CallbackQueryHandler(show_registry_projects_page, pattern=r"^reg:projpage:\d+$"))
    application.add_handler(CallbackQueryHandler(pick_project, pattern=r"^reg:pick:\d+$"))
    application.add_handler(CallbackQueryHandler(show_tab, pattern=r"^reg:tab:\d+:\w+(:\d+)?$"))
