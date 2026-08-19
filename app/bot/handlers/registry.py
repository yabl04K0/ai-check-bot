"""📜 Реестр — 3 вкладки (Открыто/Отложено/Never), источник — файлы в репо."""

from __future__ import annotations

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.bot.keyboards import back_button, registry_tabs
from app.db.models import Finding, FindingStatus, Project
from app.db.session import get_session
from app.registry_store.sync import sync_project_findings
from app.tasks.project_context import local_path as project_local_path
from app.tasks.types import SEVERITY_EMOJI

STATUS_TITLES = {"open": "🔴 ОТКРЫТО", "later": "🟡 ОТЛОЖЕНО", "never": "⚫ NEVER"}


async def show_registry_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    if not projects:
        await query.edit_message_text("Проектов пока нет.", reply_markup=back_button())
        return
    rows = [[InlineKeyboardButton(p.name, callback_data=f"reg:pick:{p.id}")] for p in projects]
    rows.append([back_button()])
    await query.edit_message_text("Какой проект?", reply_markup=InlineKeyboardMarkup(rows))


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
    _, _, project_id_raw, status = query.data.split(":")
    project_id = int(project_id_raw)
    target_status = FindingStatus(status)

    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=back_button("menu:registry"))
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

    if not findings:
        text = f"📜 {name} — {STATUS_TITLES[status]} (0){stale_note}"
    else:
        lines = [f"📜 {name} — {STATUS_TITLES[status]} ({len(findings)}){stale_note}"]
        for f in findings[:20]:
            emoji = SEVERITY_EMOJI.get(f.severity, "") if status == "open" else ""
            extra = f" · attempts={f.attempts}" if status == "open" else f" · {f.reason or ''}"
            lines.append(f"{emoji} {f.file_symbol}{extra}")
        if len(findings) > 20:
            lines.append(f"…и ещё {len(findings) - 20}")
        text = "\n".join(lines)

    await query.edit_message_text(text, reply_markup=registry_tabs(project_id))


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_registry_projects, pattern=r"^menu:registry$"))
    application.add_handler(CallbackQueryHandler(pick_project, pattern=r"^reg:pick:\d+$"))
    application.add_handler(CallbackQueryHandler(show_tab, pattern=r"^reg:tab:\d+:\w+$"))
