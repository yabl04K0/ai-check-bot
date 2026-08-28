"""🔴 ЧЕК / 🟢 LITE ЧЕК / ✨🔧♻️📝 — общий флоу запуска задачи и отчёт.

Флоу-состояние живёт в context.user_data["flow"] на время диалога (выбор
проектов → скоуп → комментарий → 🤖 ИИ для задачи → подтверждение), после
enqueue очищается. "ИИ для задачи" — per-job оверрайд тиров (см.
JobAccountTierAssignment, app.providers.tiers), необязательный шаг: ничего
не отмечено -> задача берёт аккаунты из глобальных Настроек, как раньше.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.bot.job_runner import ARCHIVE_REQUESTS, CANCEL_REQUESTS, PAUSE_REQUESTS, start_job
from app.bot.keyboards import (
    comment_menu,
    commit_confirm_menu,
    confirm_menu,
    confirm_row,
    nav_row,
    project_multiselect,
    report_menu,
    scope_menu,
)
from app.db.models import (
    AccountPriority,
    HistoryEntry,
    Job,
    JobStatus,
    Project,
    ProviderMode,
    ProviderName,
    TaskType,
)
from app.db.session import get_session
from app.github_integration.client import GitHubClient, GitHubError
from app.github_integration.token_store import resolve_github_token
from app.logging_setup import log_action
from app.providers.tiers import TIER_CYCLE, TIER_ICON, TIER_RU_NAME, all_known_accounts, set_job_tier
from app.registry_store.store import move_finding
from app.registry_store.sync import sync_project_findings
from app.tasks.branching import topic_branch_name
from app.tasks.patch_apply import apply_patch, commit_all, create_topic_branch, current_commit_sha
from app.tasks.project_context import local_path as project_local_path
from app.tasks.queue import JobQueue
from app.tasks.types import REQUIRES_DESCRIPTION, TASK_TYPE_LABELS

CHECK_TYPES = {TaskType.CHECK_FULL, TaskType.CHECK_LITE}

# Тексты кнопок scope_menu() — сводка перед подтверждением раньше
# показывала сырой internal-ключ ("all_ignore_registry") вместо того, что
# пользователь реально нажал (см. аудит меню). "path:..." не мапим —
# он и так читаем (см. scope_util.path_filter).
SCOPE_LABELS = {"all": "Всё", "all_ignore_registry": "ЧЕК всё (игнор отложенного)"}


def _format_scope(scope: str | None) -> str | None:
    if not scope:
        return None
    return SCOPE_LABELS.get(scope, scope)


def _format_tier_overrides(overrides: dict[str, AccountPriority]) -> str | None:
    if not overrides:
        return None
    parts = [f"{TIER_ICON[priority]} {key}" for key, priority in overrides.items()]
    return ", ".join(parts)


def _build_confirm_summary(flow: dict) -> str:
    task_type = flow["task_type"]
    lines = [f"✅ {TASK_TYPE_LABELS[task_type]}", f"Проектов: {len(flow.get('selected', ()))}"]
    scope_label = _format_scope(flow.get("scope"))
    if scope_label:
        lines.append(f"Скоуп: {scope_label}")
    if flow.get("comment"):
        lines.append(f"Комментарий: {flow['comment']}")
    tier_summary = _format_tier_overrides(flow.get("tier_overrides") or {})
    lines.append(
        f"ИИ для задачи: {tier_summary}" if tier_summary else "ИИ для задачи: настройки по умолчанию"
    )
    return "\n".join(lines)


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
            reply_markup=InlineKeyboardMarkup([nav_row("menu:main")]),
        )
        return

    context.user_data["flow"] = {
        "task_type": task_type,
        "selected": set(),
        "scope": None,
        "comment": None,
        "tier_overrides": {},
    }
    label = TASK_TYPE_LABELS[task_type]
    await query.edit_message_text(
        f"{label}\nПроект(ы)? (мультивыбор)", reply_markup=project_multiselect(projects, set())
    )


async def toggle_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # answer() ровно один раз за callback (повторный Telegram отвергает) —
    # сперва проверяем состояние, потом отвечаем нужным текстом один раз.
    query = update.callback_query
    flow = _flow(context)
    if "task_type" not in flow:
        await query.answer("Сессия выбора устарела, начни заново из меню.", show_alert=True)
        return
    await query.answer()
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


async def back_to_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """◀️ Назад с экрана скоупа/комментария/описания — на выбор проектов,
    БЕЗ сброса уже отмеченных (flow['selected'] переживает переход, в
    отличие от старого back_button(), который вёл прямиком в menu:main и
    стирал весь прогресс визарда)."""
    query = update.callback_query
    flow = _flow(context)
    if "task_type" not in flow:
        await query.answer("Сессия выбора устарела, начни заново из меню.", show_alert=True)
        return
    await query.answer()
    context.user_data["awaiting"] = None
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    await query.edit_message_text(
        f"{TASK_TYPE_LABELS[flow['task_type']]}\nПроект(ы)? (мультивыбор)",
        reply_markup=project_multiselect(projects, flow.get("selected", set())),
    )


def render_project_multiselect(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup] | None:
    """Текст+клавиатура экрана выбора проектов визарда — переиспользуется
    из app.bot.handlers.projects, когда пользователь добавляет новый
    проект ПРЯМО ИЗ визарда (кнопка "➕ Добавить проект" внутри
    мультивыбора) и должен вернуться на этот же экран с уже отмеченным
    выбором, а не улететь на самостоятельный экран управления проектами,
    теряя flow['selected'] (см. аудит меню). None — если сейчас нет
    активного визарда выбора проектов (flow пуст/устарел)."""
    flow = context.user_data.get("flow") or {}
    if "task_type" not in flow:
        return None
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
    text = f"{TASK_TYPE_LABELS[flow['task_type']]}\nПроект(ы)? (мультивыбор)"
    markup = project_multiselect(projects, flow.get("selected", set()))
    return text, markup


async def back_to_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """◀️ Назад с экрана комментария (для типов ЧЕК) — на выбор скоупа."""
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = None
    await query.edit_message_text("Скоуп?", reply_markup=scope_menu())


async def back_to_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """◀️ Назад с экрана "🤖 ИИ для задачи" (см. _ai_picker_view) — на
    комментарий, предпоследний шаг визарда и для типов ЧЕК
    (проекты→скоуп→комментарий→ИИ→подтверждение), и для остальных
    (проекты→комментарий→ИИ→подтверждение). Раньше confirm_menu был
    единственным шагом визарда без пути назад — только "✖ Отмена",
    стиравшая весь прогресс (см. аудит меню)."""
    query = update.callback_query
    flow = _flow(context)
    if "task_type" not in flow:
        await query.answer("Сессия выбора устарела, начни заново из меню.", show_alert=True)
        return
    await query.answer()
    task_type = flow["task_type"]
    context.user_data["awaiting"] = "comment"
    if task_type in CHECK_TYPES:
        await query.edit_message_text(
            "💬 Комментарий? Что чекать / не чекать / что пофиксить. Можно пропустить.",
            reply_markup=comment_menu(back_target="chk:back:scope"),
        )
    else:
        await query.edit_message_text(
            f"💬 Опиши задачу ({TASK_TYPE_LABELS[task_type]}) — это обязательно.",
            reply_markup=InlineKeyboardMarkup([nav_row("chk:back:projects")]),
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
            reply_markup=InlineKeyboardMarkup([nav_row("chk:back:projects")]),
        )


async def pick_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    flow = _flow(context)
    scope_key = query.data.split(":")[-1]
    if scope_key == "module":
        context.user_data["awaiting"] = "scope_module"
        await query.edit_message_text(
            "Укажи путь файла/модуля текстом:",
            reply_markup=InlineKeyboardMarkup([nav_row("chk:back:scope")]),
        )
        return
    flow["scope"] = scope_key
    context.user_data["awaiting"] = "comment"
    await query.edit_message_text(
        "💬 Комментарий? Что чекать / не чекать / что пофиксить. Можно пропустить.",
        reply_markup=comment_menu(back_target="chk:back:scope"),
    )


async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text, markup = _ai_picker_view(context)
    await query.edit_message_text(text, reply_markup=markup)


def _ai_picker_view(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Экран визарда "🤖 ИИ для этой задачи?" — между комментарием и
    подтверждением (см. запрос пользователя: "при включении задачи как и
    комментарии к задаче пусть будет список с иишками которые будут
    работать с проектом и задача приоритета на этом этапе"). Необязателен:
    ничего не отмечено -> flow["tier_overrides"] пуст -> confirm() не пишет
    ни одной JobAccountTierAssignment -> тир-роутинг этой задачи целиком
    падает на глобальные Настройки, как будто экрана вообще не было (см.
    app.providers.tiers.job_has_tier_overrides). Отмечен хотя бы один
    аккаунт -> задача использует ТОЛЬКО отмеченные, остальные исключены —
    даже если у них есть глобальный тир."""
    flow = _flow(context)
    registry = context.application.bot_data["provider_registry"]
    accounts = all_known_accounts(registry)
    overrides: dict[str, AccountPriority] = flow.setdefault("tier_overrides", {})

    lines = [
        "🤖 ИИ для этой задачи (необязательно).",
        "Тапни по аккаунту, чтобы задать его приоритет ТОЛЬКО на этот прогон: "
        "👑 Глава — план/критика, ⚖️ Средний — фиксы/тесты, 🤖 Делегация — параллельный скан.",
    ]
    if overrides:
        lines.append("Аккаунты БЕЗ отметки в этой задаче участвовать не будут.")
    else:
        lines.append(
            "Ничего не отмечено — задача возьмёт аккаунты из "
            "⚙️ Настройки → 🎚 Приоритеты аккаунтов."
        )

    rows = []
    if not accounts:
        lines.append("\nНет подключённых аккаунтов ни у одного провайдера.")
    for account in accounts:
        key = f"{account.provider.value}:{account.account_label}"
        tier = overrides.get(key)
        icon = TIER_ICON.get(tier, "➖")
        tier_name = TIER_RU_NAME.get(tier, "не задан")
        rows.append(
            [
                InlineKeyboardButton(
                    f"{icon} {key} — {tier_name}",
                    callback_data=f"chk:ai:cycle:{account.provider.value}:{account.account_label}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("Далее ✅", callback_data="chk:ai:next")])
    rows.append(nav_row("chk:back:comment"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def cycle_flow_tier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    flow = _flow(context)
    if "task_type" not in flow:
        await query.answer("Сессия выбора устарела, начни заново из меню.", show_alert=True)
        return
    payload = query.data[len("chk:ai:cycle:") :]
    provider_str, account_label = payload.split(":", 1)
    key = f"{provider_str}:{account_label}"
    overrides: dict[str, AccountPriority] = flow.setdefault("tier_overrides", {})
    next_tier = TIER_CYCLE[overrides.get(key)]
    if next_tier is None:
        overrides.pop(key, None)
    else:
        overrides[key] = next_tier
    await query.answer(f"→ {TIER_RU_NAME.get(next_tier, 'не задан')}")
    text, markup = _ai_picker_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def ai_picker_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _show_confirm(query, context)


async def back_to_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """◀️ Назад с экрана подтверждения — на экран выбора ИИ."""
    query = update.callback_query
    flow = _flow(context)
    if "task_type" not in flow:
        await query.answer("Сессия выбора устарела, начни заново из меню.", show_alert=True)
        return
    await query.answer()
    text, markup = _ai_picker_view(context)
    await query.edit_message_text(text, reply_markup=markup)


async def _show_confirm(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = _flow(context)
    await query.edit_message_text(_build_confirm_summary(flow), reply_markup=confirm_menu(flow["task_type"]))


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
        ai_text, ai_markup = _ai_picker_view(context)
        await update.message.reply_text(ai_text, reply_markup=ai_markup)
        return

    if awaiting == "scope_module":
        flow = _flow(context)
        flow["scope"] = f"path:{update.message.text.strip()}"
        context.user_data["awaiting"] = "comment"
        await update.message.reply_text(
            "💬 Комментарий? Можно пропустить.", reply_markup=comment_menu(back_target="chk:back:scope")
        )
        return

    if awaiting == "job_note":
        job_id = context.user_data.get("job_note_id")
        context.user_data["awaiting"] = None
        context.user_data.pop("job_note_id", None)
        if job_id is None:
            return
        text = update.message.text.strip()
        if not text:
            return
        with get_session() as session:
            job = session.get(Job, job_id)
            if job is not None:
                JobQueue(session).add_live_note(job, text)
        await update.message.reply_text("✅ Добавлено.")
        return

    if awaiting == "later_reason":
        await _do_move_finding(update, context, to="later")
        return
    if awaiting == "never_reason":
        await _do_move_finding(update, context, to="never")
        return
    if awaiting == "fix_select":
        job_id = context.user_data.get("fix_select_job_id")
        if job_id is None:
            context.user_data["awaiting"] = None
            return
        text = update.message.text.strip()
        if not text:
            # TaskType.FIX входит в REQUIRES_DESCRIPTION — пустое описание
            # тратит вызов ИИ впустую. Не сбрасываем awaiting/job_id, чтобы
            # можно было просто прислать текст ещё раз (см. аудит меню).
            await update.message.reply_text("Описание обязательно, отправь текст ещё раз.")
            return
        context.user_data.pop("fix_select_job_id", None)
        context.user_data["awaiting"] = None
        await _enqueue_fix(update, context, job_id, text)
        return


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    flow = _flow(context)
    task_type: TaskType = flow["task_type"]
    project_ids = list(flow.get("selected", ()))
    scope = flow.get("scope")
    comment = flow.get("comment")
    tier_overrides: dict[str, AccountPriority] = flow.get("tier_overrides") or {}

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

    for key, priority in tier_overrides.items():
        provider_str, account_label = key.split(":", 1)
        set_job_tier(job_id, ProviderName(provider_str), account_label, priority)

    context.user_data.pop("flow", None)
    context.user_data["awaiting"] = None

    if busy:
        await query.edit_message_text(f"⏳ Задача #{job_id} встала в очередь, позиция {position}.")
        return

    await query.edit_message_text(f"✅ Задача #{job_id} запускается…")
    asyncio.create_task(start_job(context.application, job_id))


async def approve_job_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тап "✅ Разрешить" на экране подтверждения запуска (см.
    app.bot.job_runner._request_start_approval, показывается только пока
    включён доступ ИИ к GITHUB_TOKEN и выключено автоодобрение)."""
    from app.bot.job_runner import APPROVED_JOB_IDS

    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    APPROVED_JOB_IDS.add(job_id)
    log_action(str(update.effective_user.id), "job_start_approved", f"#{job_id}")
    await query.answer("Разрешено")
    await query.edit_message_text(f"✅ Задача #{job_id} запускается…")
    asyncio.create_task(start_job(context.application, job_id))


async def reject_job_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is not None and job.status == JobStatus.QUEUED:
            JobQueue(session).mark_cancelled(job)
    log_action(str(update.effective_user.id), "job_start_rejected", f"#{job_id}")
    await query.answer("Отклонено")
    await query.edit_message_text(f"❌ Задача #{job_id} отклонена, запуск отменён.")


async def cancel_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    CANCEL_REQUESTS.add(job_id)
    await query.answer("Отменяю…")


async def pause_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    PAUSE_REQUESTS.add(job_id)
    # Немедленная обратная связь в UI — пайплайн сам подтвердит статус между
    # шагами (см. Pipeline._wait_while_paused), текущий шаг не прерывается.
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is not None and job.status == JobStatus.RUNNING:
            job.status = JobStatus.PAUSED_MANUAL
            session.commit()
    await query.answer("⏸ Ставлю на паузу…")


async def resume_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    PAUSE_REQUESTS.discard(job_id)
    await query.answer("▶️ Продолжаю…")


async def prompt_job_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    context.user_data["awaiting"] = "job_note"
    context.user_data["job_note_id"] = job_id
    await query.answer()
    await context.bot.send_message(
        update.effective_chat.id, "💬 Пришли текст — добавлю его к задаче, ИИ увидит на следующем шаге."
    )


async def archive_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    job_id = int(query.data.split(":")[-1])
    ARCHIVE_REQUESTS.add(job_id)
    CANCEL_REQUESTS.add(job_id)
    await query.answer("📦 Останавливаю и собираю хендовер…")


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
    """Необратимое действие (запускает ИИ, который правит файлы и может
    закоммитить) — раньше срабатывало сразу по одному тапу, теперь как
    commit_ask/commit_yes требует явного подтверждения."""
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    await query.edit_message_text(
        f"🔧 Применить все фиксы из отчёта #{job_id}?",
        reply_markup=InlineKeyboardMarkup(
            [confirm_row(f"report:fix_all_yes:{job_id}", f"report:fix_all_no:{job_id}")]
        ),
    )


async def report_fix_all_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    await _enqueue_fix(update, context, job_id, f"Примени фиксы из отчёта задачи #{job_id}.")


async def report_fix_all_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Отменено")
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        is_check = job.task_type in CHECK_TYPES if job else True
    await query.edit_message_text(f"Отчёт #{job_id}", reply_markup=report_menu(job_id, is_check=is_check))


async def report_fix_select_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    context.user_data["awaiting"] = "fix_select"
    context.user_data["fix_select_job_id"] = job_id
    await context.bot.send_message(
        update.effective_chat.id, "Опиши текстом, что именно фиксить из отчёта."
    )


async def _enqueue_fix(
    update: Update, context: ContextTypes.DEFAULT_TYPE, source_job_id: int, comment: str
) -> None:
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
        await context.bot.send_message(
            update.effective_chat.id, f"⏳ Фикс поставлен в очередь, позиция {position}."
        )
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
        update.effective_chat.id,
        "Отправь: `file::symbol; причина` — отложить эту находку.",
        parse_mode="Markdown",
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
    raw = update.message.text.strip()
    parts = raw.split(";", 1)
    reason = parts[1].strip() if len(parts) > 1 else ""
    if len(parts) < 2 or not reason:
        # awaiting/registry_job_id НЕ трогаем при ошибке формата — раньше
        # сбрасывались ДО этой проверки, и повторная попытка пользователя
        # (именно то, что просит подсказка) молча терялась: ни одна ветка
        # on_text уже не совпадала (см. аудит меню).
        await update.message.reply_text("Формат: `file::symbol; причина`.", parse_mode="Markdown")
        return
    context.user_data["awaiting"] = None
    job_id = context.user_data.pop("registry_job_id", None)
    if job_id is None:
        await update.message.reply_text("Сессия устарела — начни заново с кнопки в отчёте.")
        return
    file_symbol = parts[0].strip()

    with get_session() as session:
        job = session.get(Job, job_id)
        projects = list(job.projects) if job else []

    moved_any = False
    with get_session() as session:
        for project in projects:
            path = project_local_path(project)
            if path is None:
                continue
            if move_finding(path, file_symbol, to=to, reason=reason):
                moved_any = True
                project = session.merge(project)
                sync_project_findings(session, project)
        session.commit()

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
        has_patch = bool(job.patch_text and job.patch_text.strip())
    hint = "" if has_patch else "\n(патч не сгенерирован — нечего применять)"
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
    await query.edit_message_text(f"Отчёт #{job_id}", reply_markup=report_menu(job_id, is_check=is_check))


def _apply_and_commit_blocking(job_id: int, github_token: str | None) -> str:
    """Блокирующая часть (git apply/commit/push) — выполняется в отдельном
    потоке через asyncio.to_thread, чтобы не подвешивать event loop бота.
    Возвращает готовый текст сообщения для пользователя."""
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return "Задача не найдена."
        patch_text = job.patch_text
        projects = list(job.projects)
        task_type = job.task_type
        comment = job.comment

        if not patch_text or not patch_text.strip():
            return f"⚠️ Патч для #{job_id} пуст — нечего применять."

        target = next((p for p in projects if project_local_path(p)), None)
        if target is None:
            return (
                "Ни у одного проекта из задачи не задан local_path (локальный "
                "чекаут) — применить патч некуда."
            )
        path = project_local_path(target)

        # BRANCHING gate (см. app/tasks/branching.py): этот пайплайн не
        # производит доказательство GATE-CONFIDENT (критик-пасс, red→green
        # регресс-тест) — коммит бота изолируется в свою топик-ветку, а не
        # ложится прямо в ветку, которая была открыта у человека.
        branch_name = topic_branch_name(job)
        branch_ok, branch_detail = create_topic_branch(path, branch_name)
        if not branch_ok:
            return f"❌ Не удалось создать ветку {branch_name} в {target.name}:\n{branch_detail[:1000]}"

        ok, apply_detail = apply_patch(path, patch_text)
        if not ok:
            return (
                f"❌ Не удалось применить патч в {target.name} (ветка {branch_name}):\n"
                f"{apply_detail[:1500]}"
            )

        short_comment = (comment or "изменения от ai-check-bot")[:72]
        task_label = TASK_TYPE_LABELS.get(task_type, str(task_type))
        commit_message = f"{task_label}: {short_comment}"
        ok, commit_detail = commit_all(path, commit_message)
        if not ok:
            return (
                f"⚠️ Патч применён в {target.name} (ветка {branch_name}), но commit не удался:\n"
                f"{commit_detail[:1500]}"
            )

        log_action(
            str(job.created_by_tg_id or "system"), "commit_applied", f"job #{job_id} project={target.name}"
        )

        sha = current_commit_sha(path)
        commit_url = f"https://github.com/{target.repo_full_name}/commit/{sha}" if sha else None
        history_entry = session.scalar(
            select(HistoryEntry)
            .where(HistoryEntry.job_id == job_id, HistoryEntry.project_id == target.id)
            .order_by(HistoryEntry.created_at.desc())
        )
        if history_entry is not None and commit_url:
            history_entry.commit_url = commit_url
        session.commit()

        push_note = ""
        if target.is_self:
            push_note = (
                "\n\n⚠️ self-check: пуш НЕ выполняется автоматически, даже если "
                "автопуш разрешён для других проектов — запушь вручную через 🐙 GitHub."
            )
        elif target.autopush_enabled:
            if not github_token:
                push_note = "\n\n(автопуш включён для проекта, но GITHUB_TOKEN не задан)"
            else:
                try:
                    client = GitHubClient(github_token)
                    push_result = client.push_commit(path, branch=branch_name)
                    push_note = f"\n\n✅ Запушено в {branch_name}: {push_result or 'ok'}"
                    log_action(str(job.created_by_tg_id or "system"), "commit_pushed", target.repo_full_name)
                except GitHubError as exc:
                    push_note = f"\n\n⚠️ Коммит создан, но push не удался: {exc}"

        return (
            f"✅ Закоммичено в {target.name}, ветка {branch_name}"
            f"{f' ({commit_url})' if commit_url else ''}.\n"
            f"Это отдельная ветка от текущей — смержи в свою основную вручную, когда проверишь."
            f"{push_note}"
        )


async def commit_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    await query.edit_message_text(f"⏳ Применяю патч и коммичу для #{job_id}…")

    settings = context.application.bot_data["settings"]
    text = await asyncio.to_thread(
        _apply_and_commit_blocking, job_id, resolve_github_token(settings)
    )
    await context.bot.send_message(
        update.effective_chat.id, text[:4000], reply_markup=InlineKeyboardMarkup([nav_row("menu:main")])
    )


async def commit_show_diff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = int(query.data.split(":")[-1])
    with get_session() as session:
        job = session.get(Job, job_id)
        text = job.patch_text or job.report_text or "(пусто)"
    for i in range(0, len(text), 3800):
        await context.bot.send_message(update.effective_chat.id, text[i : i + 3800])


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(start_flow, pattern=r"^chk:start:\w+$"))
    application.add_handler(CallbackQueryHandler(toggle_project, pattern=r"^chk:proj:\d+$"))
    application.add_handler(CallbackQueryHandler(projects_next, pattern=r"^chk:proj:next$"))
    application.add_handler(CallbackQueryHandler(back_to_projects, pattern=r"^chk:back:projects$"))
    application.add_handler(CallbackQueryHandler(back_to_scope, pattern=r"^chk:back:scope$"))
    application.add_handler(CallbackQueryHandler(back_to_comment, pattern=r"^chk:back:comment$"))
    application.add_handler(CallbackQueryHandler(pick_scope, pattern=r"^chk:scope:\w+$"))
    application.add_handler(CallbackQueryHandler(skip_comment, pattern=r"^chk:comment:skip$"))
    application.add_handler(CallbackQueryHandler(cycle_flow_tier, pattern=r"^chk:ai:cycle:.+$"))
    application.add_handler(CallbackQueryHandler(ai_picker_next, pattern=r"^chk:ai:next$"))
    application.add_handler(CallbackQueryHandler(back_to_ai, pattern=r"^chk:back:ai$"))
    application.add_handler(CallbackQueryHandler(confirm, pattern=r"^chk:confirm$"))
    application.add_handler(CallbackQueryHandler(approve_job_start, pattern=r"^job:approve:\d+$"))
    application.add_handler(CallbackQueryHandler(reject_job_start, pattern=r"^job:reject:\d+$"))
    application.add_handler(CallbackQueryHandler(cancel_job, pattern=r"^job:cancel:\d+$"))
    application.add_handler(CallbackQueryHandler(pause_job, pattern=r"^job:pause:\d+$"))
    application.add_handler(CallbackQueryHandler(resume_job, pattern=r"^job:resume:\d+$"))
    application.add_handler(CallbackQueryHandler(prompt_job_note, pattern=r"^job:note:\d+$"))
    application.add_handler(CallbackQueryHandler(archive_job, pattern=r"^job:archive:\d+$"))
    application.add_handler(CallbackQueryHandler(report_details, pattern=r"^report:details:\d+$"))
    application.add_handler(CallbackQueryHandler(report_fix_all, pattern=r"^report:fix_all:\d+$"))
    application.add_handler(CallbackQueryHandler(report_fix_all_yes, pattern=r"^report:fix_all_yes:\d+$"))
    application.add_handler(CallbackQueryHandler(report_fix_all_no, pattern=r"^report:fix_all_no:\d+$"))
    application.add_handler(
        CallbackQueryHandler(report_fix_select_prompt, pattern=r"^report:fix_select:\d+$")
    )
    application.add_handler(CallbackQueryHandler(report_later_prompt, pattern=r"^report:later:\d+$"))
    application.add_handler(CallbackQueryHandler(report_never_prompt, pattern=r"^report:never:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_ask, pattern=r"^commit:ask:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_yes, pattern=r"^commit:yes:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_no, pattern=r"^commit:no:\d+$"))
    application.add_handler(CallbackQueryHandler(commit_show_diff, pattern=r"^commit:diff:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=0)
