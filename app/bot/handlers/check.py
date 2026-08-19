"""🔴 ЧЕК / 🟢 LITE ЧЕК / ✨🔧♻️📝 — общий флоу запуска задачи и отчёт.

Флоу-состояние живёт в context.user_data["flow"] на время диалога (выбор
проектов → скоуп → комментарий → подтверждение), после enqueue очищается.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.job_runner import CANCEL_REQUESTS, start_job
from app.bot.keyboards import (
    back_button,
    comment_menu,
    commit_confirm_menu,
    confirm_menu,
    project_multiselect,
    scope_menu,
)
from app.db.models import Job, Project, ProviderMode, TaskType
from app.db.session import get_session
from app.registry_store.store import move_finding
from app.tasks.project_context import local_path as project_local_path
from app.tasks.queue import JobQueue
from app.tasks.types import REQUIRES_DESCRIPTION, TASK_TYPE_LABELS

CHECK_TYPES = {TaskType.CHECK_FULL, TaskType.CHECK_LITE}


def _flow(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("flow", {})


async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    task_type = TaskType(query.data.split(":")[-1])

    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()

    if not projects:
        await query.edit_message_text(
            "Нет ни одного проекта. Сначала добавь его в 📁 Проекты.",
            reply_markup=back_button(),
        )
        return

    context.user_data["flow"] = {"task_type": task_type, "selected": set(), "scope": None, "comment": None}
    label = TASK_TYPE_LABELS[task_type]
    await query.edit_message_text(
        f"{label}\nПроект(ы)? (мультивыбор)", reply_markup=project_multiselect(projects, set())
    )


async def toggle_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    flow = _flow(context)
    if "task_type" not in flow:
        await query.answer("Сессия выбора устарела, начни заново из меню.", show_alert=True)
        return
    project_id = int(query.data.split(":")[-1])
    selected: set[int] = flow.setdefault("selected", set())
    if project_id in selected:
        selected.discard(project_id)
    else:
        selected.add(project_id)

    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    await query.edit_message_text(
        f"{TASK_TYPE_LABELS[flow['task_type']]}\nПроект(ы)? (мультивыбор)",
        reply_markup=project_multiselect(projects, selected),
    )


async def projects_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    flow = _flow(context)
    if not flow.get("selected"):
        await query.answer("Выбери хотя бы один проект.", show_alert=True)
        return
    await query.answer()
    task_type = flow["task_type"]

    if task_type in CHECK_TYPES:
        await query.edit_message_text("Скоуп?", reply_markup=scope_menu())
    else:
        flow["scope"] = None
        context.user_data["awaiting"] = "comment"
        await query.edit_message_text(
            f"💬 Опиши задачу ({TASK_TYPE_LABELS[task_type]}) — это обязательно.",
        )


async def pick_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    flow = _flow(context)
    scope_key = query.data.split(":")[-1]
    if scope_key == "module":
        context.user_data["awaiting"] = "scope_module"
        await query.edit_message_text("Укажи путь файла/модуля текстом:")
        return
    flow["scope"] = scope_key
    context.user_data["awaiting"] = "comment"
    await query.edit_message_text(
        "💬 Комментарий? Что чекать / не чекать / что пофиксить. Можно пропустить.",
        reply_markup=comment_menu(),
    )


async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _show_confirm(query, context)


async def _show_confirm(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = _flow(context)
    task_type = flow["task_type"]
    label = TASK_TYPE_LABELS[task_type]
    lines = [f"✅ {label}", f"Проектов: {len(flow.get('selected', ()))}"]
    if flow.get("scope"):
        lines.append(f"Скоуп: {flow['scope']}")
    if flow.get("comment"):
        lines.append(f"Комментарий: {flow['comment']}")
    await query.edit_message_text("\n".join(lines), reply_markup=confirm_menu(task_type))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")
    if awaiting == "comment":
        flow = _flow(context)
        text = update.message.text.strip()
        if flow["task_type"] in REQUIRES_DESCRIPTION and not text:
            await update.message.reply_text("Описание обязательно для этого типа задачи, отправь текст.")
            return
        flow["comment"] = text or None
        context.user_data["awaiting"] = None
        task_type = flow["task_type"]
        label = TASK_TYPE_LABELS[task_type]
        lines = [f"✅ {label}", f"Проектов: {len(flow.get('selected', ()))}"]
        if flow.get("scope"):
            lines.append(f"Скоуп: {flow['scope']}")
        if flow.get("comment"):
            lines.append(f"Комментарий: {flow['comment']}")
        await update.message.reply_text("\n".join(lines), reply_markup=confirm_menu(task_type))
        return

    if awaiting == "scope_module":
        flow = _flow(context)
        flow["scope"] = f"path:{update.message.text.strip()}"
        context.user_data["awaiting"] = "comment"
        await update.message.reply_text(
            "💬 Комментарий? Можно пропустить.", reply_markup=comment_menu()
        )
        return

    if awaiting == "later_reason":
        await _do_move_finding(update, context, to="later")
        return
    if awaiting == "never_reason":
        await _do_move_finding(update, context, to="never")
        return
    if awaiting == "fix_select":
        job_id = context.user_data.pop("fix_select_job_id", None)
        context.user_data["awaiting"] = None
        if job_id is None:
            return
        await _enqueue_fix(update, context, job_id, update.message.text.strip())
        return


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    flow = _flow(context)
    task_type: TaskType = flow["task_type"]
    project_ids = list(flow.get("selected", ()))
    scope = flow.get("scope")
    comment = flow.get("comment")

    with get_session() as session:
        queue = JobQueue(session)
        job = queue.enqueue(
            task_type,
            project_ids,
            provider_mode=ProviderMode.AUTO,
            scope=scope,
            comment=comment,
            created_by_tg_id=update.effective_chat.id,
        )
        job_id = job.id
        busy = queue.is_busy()
        position = queue.position_in_queue(job_id)

    context.user_data.pop("flow", None)
    context.user_data["awaiting"] = None

    if busy:
        await query.edit_message_text(f"⏳ Задача #{job_id} встала в очередь, позиция {position}.")
        return

    await query.edit_message_text(f"✅ Задача #{job_id} запускается…")
    asyncio.create_task(start_job(context.application, job_id))


async def cancel_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    CANCEL_REQUESTS.add(job_id)
    await query.answer("Отменяю…")


async def report_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        text = job.report_text if job else None
    if not text:
        await context.bot.send_message(update.effective_chat.id, "Отчёт пуст.")
        return
    for i in range(0, len(text), 3800):
        await context.bot.send_message(update.effective_chat.id, text[i : i + 3800])


async def report_fix_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    await _enqueue_fix(update, context, job_id, f"Примени фиксы из отчёта задачи #{job_id}.")


async def report_fix_select_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    context.user_data["awaiting"] = "fix_select"
    context.user_data["fix_select_job_id"] = job_id
    await context.bot.send_message(
        update.effective_chat.id, "Опиши текстом, что именно фиксить из отчёта."
    )


async def _enqueue_fix(update: Update, context: ContextTypes.DEFAULT_TYPE, source_job_id: int, comment: str) -> None:
    with get_session() as session:
        source = session.get(Job, source_job_id)
        if source is None:
            await context.bot.send_message(update.effective_chat.id, "Исходная задача не найдена.")
            return
        project_ids = [p.id for p in source.projects]
        queue = JobQueue(session)
        job = queue.enqueue(
            TaskType.FIX,
            project_ids,
            provider_mode=ProviderMode.AUTO,
            comment=comment,
            created_by_tg_id=update.effective_chat.id,
        )
        job_id = job.id
        busy = queue.is_busy()
        position = queue.position_in_queue(job_id)

    if busy and position > 1:
        await context.bot.send_message(update.effective_chat.id, f"⏳ Фикс поставлен в очередь, позиция {position}.")
        return
    await context.bot.send_message(update.effective_chat.id, f"✅ Фикс #{job_id} запускается…")
    asyncio.create_task(start_job(context.application, job_id))


async def report_later_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    context.user_data["awaiting"] = "later_reason"
    context.user_data["registry_job_id"] = job_id
    await context.bot.send_message(
        update.effective_chat.id, "Отправь: `file::symbol; причина` — отложить эту находку.", parse_mode="Markdown"
    )


async def report_never_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    context.user_data["awaiting"] = "never_reason"
    context.user_data["registry_job_id"] = job_id
    await context.bot.send_message(
        update.effective_chat.id,
        "Отправь: `file::symbol; причина (не баг/фича/не трогать)`.",
        parse_mode="Markdown",
    )


async def _do_move_finding(update: Update, context: ContextTypes.DEFAULT_TYPE, *, to: str) -> None:
    context.user_data["awaiting"] = None
    job_id = context.user_data.pop("registry_job_id", None)
    raw = update.message.text.strip()
    if ";" not in raw or job_id is None:
        await update.message.reply_text("Формат: `file::symbol; причина`.", parse_mode="Markdown")
        return
    file_symbol, reason = (p.strip() for p in raw.split(";", 1))

    with get_session() as session:
        job = session.get(Job, job_id)
        projects = list(job.projects) if job else []

    moved_any = False
    for project in projects:
        path = project_local_path(project)
        if path is None:
            continue
        if move_finding(path, file_symbol, to=to, reason=reason):
            moved_any = True

    if moved_any:
        await update.message.reply_text(f"✅ Перенесено в {to}.")
    else:
        await update.message.reply_text(
            "Не нашёл такую находку в chek_open.md ни одного из проектов задачи "
            "(или у проекта не задан local_path)."
        )


async def commit_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        report = job.report_text or ""
    diff_present = "Патч:" in report or "Финальный фикс:" in report
    hint = "" if diff_present else "\n(патч в отчёте не найден)"
    await query.edit_message_text(
        f"💾 Зафиксить и запушить?{hint}", reply_markup=commit_confirm_menu(job_id)
    )


async def commit_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        is_check = job.task_type in CHECK_TYPES
    from app.bot.keyboards import report_menu

    await query.edit_message_text(f"Отчёт #{job_id}", reply_markup=report_menu(job_id, is_check=is_check))


async def commit_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пайплайн генерирует ТОЛЬКО текст патча (см. app/tasks/generic.py) —
    автоприменение диффа на диск и git commit сознательно не реализованы в
    этой версии (риск непроверенного patch-applier на реальном репо).
    Честно говорим об этом вместо фейкового 'закоммичено'."""
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    await query.edit_message_text(
        f"⚠️ Автоприменение патча + коммит для #{job_id} пока не реализовано.\n"
        "Возьми диф из 🔍 Детали и примени/закоммить вручную (или через `git apply`), "
        "затем запушь через 🐙 GitHub.",
        reply_markup=back_button(),
    )


async def commit_show_diff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        text = job.report_text or "(пусто)"
    for i in range(0, len(text), 3800):
        await context.bot.send_message(update.effective_chat.id, text[i : i + 3800])


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(start_flow, pattern=r"^chk:start:\w+$"))
    application.add_handler(CallbackQueryHandler(toggle_project, pattern=r"^chk:proj:\d+$"))
    application.add_handler(CallbackQueryHandler(projects_next, pattern=r"^chk:proj:next$"))
    application.add_handler(CallbackQueryHandler(pick_scope, pattern=r"^chk:scope:\w+$"))
    application.add_handler(CallbackQueryHandler(skip_comment, pattern=r"^chk:comment:skip$"))
    application.add_handler(CallbackQueryHandler(confirm, pattern=r"^chk:confirm$"))
    application.add_handler(CallbackQueryHandler(cancel_job, pattern=r"^job:cancel:\d+$"))
    application.add_handler(CallbackQueryHandler(report_details, pattern=r"^report:details:\d+$"))
    application.add_handler(CallbackQueryHandler(report_fix_all, pattern=r"^report:fix_all:\d+$"))
    application.add_handler(CallbackQueryHandler(report_fix_select_prompt, pattern=r"^report:fix_select:\d+$"))
    application.add_handler(CallbackQueryHandler(report_later_prompt, pattern=r"^report:later:\d+$"))
    application.add_handler(CallbackQueryHandler(report_never_prompt, pattern=r"^report:never:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_ask, pattern=r"^commit:ask:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_yes, pattern=r"^commit:yes:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_no, pattern=r"^commit:no:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_show_diff, pattern=r"^commit:diff:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=0)
