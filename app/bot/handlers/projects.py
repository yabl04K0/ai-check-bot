"""📁 Проекты — список, добавление, настройки проекта."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown

from app.bot.handlers.ai_chat import reset_stale_chat
from app.bot.handlers.check import render_project_multiselect
from app.bot.keyboards import confirm_row, nav_row, paginate_rows
from app.db.models import Project
from app.db.session import get_session
from app.github_integration.client import GitHubClient, GitHubError
from app.github_integration.token_store import resolve_github_token
from app.logging_setup import log_action
from app.registry_store.last_prompt import read_last_prompt, write_last_prompt
from app.registry_store.state_log import read_tail as read_state_log_tail
from app.tasks.local_repos import detect_repo_full_name, discover_local_repos
from app.tasks.patch_apply import (
    commit_all,
    current_branch,
    discard_uncommitted_changes,
    has_uncommitted_changes,
)
from app.tasks.project_context import local_path as project_local_path

ADD_PROJECT_PROMPT = (
    "Отправь одной строкой: `Имя проекта; owner/repo` "
    "(например: `Мой проект; owner/repo`).\n"
    "Локальный путь для чекаута можно добавить третьим полем через `;`."
)


def _list_projects() -> list[Project]:
    with get_session() as session:
        projects = session.scalars(select(Project).order_by(Project.id)).all()
        session.expunge_all()
        return list(projects)


def _management_menu(projects: list[Project], page: int = 0) -> tuple[InlineKeyboardMarkup, int]:
    rows = [
        [InlineKeyboardButton(f"{'🤖 ' if p.is_self else ''}{p.name}", callback_data=f"proj:manage:{p.id}")]
        for p in projects
    ]
    page_rows, total_pages = paginate_rows(rows, page, nav_prefix="proj:page")
    page_rows.append([InlineKeyboardButton("➕ Добавить проект", callback_data="proj:add")])
    page_rows.append(nav_row())
    return InlineKeyboardMarkup(page_rows), total_pages


async def show_projects(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    # Стандартная точка посадки после ◀️ Назад со всех awaiting-экранов
    # этого файла (добавление проекта, Last Prompt) — без сброса здесь
    # (раньше это делал только menu.py::show_main_menu на 🏠 Меню) уход
    # именно через "Назад" оставлял awaiting висеть, и следующее свободное
    # сообщение пользователя в ЛЮБОМ другом месте бота (например в
    # 🗨 ИИ-чате) молча перехватывалось этим on_text (см. аудит меню).
    # reset_stale_chat, не голый pop — если это был активный ИИ-чат, его
    # сессию в БД тоже надо закрыть, а не просто забыть про неё.
    reset_stale_chat(context, update.effective_user.id)
    context.user_data.pop("awaiting", None)
    projects = _list_projects()
    markup, total_pages = _management_menu(projects, page)
    page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"📁 Проекты{page_note}" if projects else "📁 Проекты — пока пусто, добавь первый."
    await query.edit_message_text(text, reply_markup=markup)


async def show_projects_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await show_projects(update, context, page=page)


def _nightly_check_label(project: Project) -> str:
    if project.nightly_check_time:
        return f"🌙 Ночная проверка: {project.nightly_check_time}"
    return "🌙 Ночная проверка: выкл"


def _project_settings_menu(project: Project) -> InlineKeyboardMarkup:
    auto_label = "🔔 Авточек: вкл" if project.autocheck_enabled else "🔔 Авточек: выкл"
    self_label = "🤖 Self-check: вкл" if project.is_self else "🤖 Self-check: выкл"
    rows = [
        [InlineKeyboardButton(auto_label, callback_data=f"proj:toggle_auto:{project.id}")],
        [InlineKeyboardButton(self_label, callback_data=f"proj:toggle_self:{project.id}")],
        [InlineKeyboardButton(_nightly_check_label(project), callback_data=f"proj:nightly:{project.id}")],
        [InlineKeyboardButton("📤 Запушить (без ИИ)", callback_data=f"proj:push:{project.id}")],
        [
            InlineKeyboardButton(
                "🗑️ Откатить незакоммиченные правки", callback_data=f"proj:discard:{project.id}"
            )
        ],
        [InlineKeyboardButton("📜 Реестр багов", callback_data=f"reg:tab:{project.id}:open")],
        [InlineKeyboardButton("📝 Last Prompt", callback_data=f"proj:lastprompt:{project.id}")],
        [InlineKeyboardButton("📒 STATE_LOG", callback_data=f"proj:statelog:{project.id}")],
        [InlineKeyboardButton("🕘 История", callback_data=f"hist:proj:{project.id}")],
        [InlineKeyboardButton("🗑️ Убрать из списка", callback_data=f"proj:del:{project.id}")],
        nav_row("menu:projects"),
    ]
    return InlineKeyboardMarkup(rows)


def _project_info_text(project: Project) -> str:
    return (
        f"⚙️ {project.name}\n"
        f"repo: {project.repo_full_name}\n"
        f"local_path: {project.local_path or '(не задан)'}\n"
        f"self-check: {'да' if project.is_self else 'нет'}\n"
        f"ночная проверка: {project.nightly_check_time or 'выкл'}"
    )


_PROJECT_NOT_FOUND_MARKUP = InlineKeyboardMarkup([nav_row("menu:projects")])


async def manage_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    # См. show_projects — та же защита от осиротевшего awaiting/ИИ-чата
    # при уходе на карточку проекта через ◀️ Назад с любого её подэкрана.
    reset_stale_chat(context, update.effective_user.id)
    context.user_data.pop("awaiting", None)
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        text = _project_info_text(project)
        markup = _project_settings_menu(project)
    await query.edit_message_text(text, reply_markup=markup)


async def toggle_autocheck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.answer()
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        project.autocheck_enabled = not project.autocheck_enabled
        session.commit()
        text = _project_info_text(project)
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
            await query.answer()
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        project.is_self = not project.is_self
        session.commit()
        text = _project_info_text(project)
        markup = _project_settings_menu(project)
    await query.answer("Ок")
    await query.edit_message_text(text, reply_markup=markup)


async def prompt_nightly_check_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        current = project.nightly_check_time

    context.user_data["awaiting"] = f"nightly_check_time:{project_id}"
    rows = []
    if current:
        rows.append(
            [InlineKeyboardButton("🚫 Отключить", callback_data=f"proj:nightly_clear:{project_id}")]
        )
    rows.append(nav_row(f"proj:manage:{project_id}"))
    text = (
        f"🌙 Ночная проверка — сейчас: {current or 'выключена'}\n\n"
        "Отправь время в формате `HH:MM` (по местному времени бота), например "
        "`03:30` — каждый день в это время будет запускаться Full ЧЕК для этого "
        "проекта независимо от квоты."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))


async def clear_nightly_check_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.answer()
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        project.nightly_check_time = None
        project.nightly_last_run_date = None
        session.commit()
        text = _project_info_text(project)
        markup = _project_settings_menu(project)
    context.user_data["awaiting"] = None
    await query.answer("Отключено")
    await query.edit_message_text(text, reply_markup=markup)


async def show_last_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """LAST_PROMPT.md проекта — тот же слот "продолжи отсюда", что читает
    Step1to4Registry/LiteStep1Orchestrator в начале прогона ЧЕКа (см.
    app/tasks/project_context.py::gather_last_prompt)."""
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        name = project.name
        path = project_local_path(project)

    if path is None:
        text = f"📝 Last Prompt — {name}\n\n(нет локального чекаута — local_path не задан)"
        rows = [nav_row(f"proj:manage:{project_id}")]
    else:
        current = read_last_prompt(path) or "(пусто)"
        text = f"📝 Last Prompt — {name}\n\n{current}"
        rows = [
            [InlineKeyboardButton("✏️ Изменить", callback_data=f"proj:lastprompt:edit:{project_id}")],
            nav_row(f"proj:manage:{project_id}"),
        ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def show_state_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Хвост STATE_LOG.md — только на чтение (см. app/registry_store/state_log.py:
    "NEVER read it whole", тут то же самое правило для UI: только последние
    строки, не весь файл). Пишет в него сам бот через HANDOVER
    (app/tasks/handover.py) и вручную его не редактируют из бота."""
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text(
                "Проект не найден.", reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")])
            )
            return
        name = project.name
        path = project_local_path(project)

    if path is None:
        text = f"📒 STATE_LOG — {name}\n\n(нет локального чекаута — local_path не задан)"
    else:
        tail = read_state_log_tail(path, max_lines=60) or "(пусто — записей ещё нет)"
        text = f"📒 STATE_LOG — {name} (последние строки)\n\n{tail}"
    await query.edit_message_text(
        text[:4000], reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")])
    )


async def prompt_edit_last_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        path = project_local_path(project)

    if path is None:
        await query.edit_message_text(
            "Нет локального чекаута — local_path не задан, некуда писать LAST_PROMPT.md.",
            reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
        )
        return

    context.user_data["awaiting"] = f"last_prompt:{project_id}"
    await query.edit_message_text(
        "Отправь текст, с которым должна продолжить следующая AI-сессия "
        "(перезапишет LAST_PROMPT.md целиком):",
        reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
    )


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

    branch = current_branch(path)
    if branch is None:
        return "⚠️ Не удалось определить текущую ветку (detached HEAD?) — некуда пушить."
    try:
        client = GitHubClient(github_token)
        push_result = client.push_commit(path, branch=branch)
    except GitHubError as exc:
        return f"❌ Push не удался: {exc}"

    log_action(str(project_id), "manual_push", repo_full_name)
    return f"✅ Запушено вручную (без ИИ): {name}, ветка {branch}\n{push_result or 'ok'}"


async def manual_push(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    settings = context.application.bot_data["settings"]
    await query.edit_message_text("⏳ Пушу без участия ИИ…")
    text = await asyncio.to_thread(
        _manual_push_blocking, project_id, resolve_github_token(settings)
    )
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")])
    )


async def prompt_discard_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            await query.edit_message_text("Проект не найден.", reply_markup=_PROJECT_NOT_FOUND_MARKUP)
            return
        name = project.name
        path = project_local_path(project)

    if path is None:
        await query.edit_message_text(
            f"⚠️ У {name} не задан local_path (или путь недоступен).",
            reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
        )
        return
    if not has_uncommitted_changes(path):
        await query.edit_message_text(
            f"✅ В {name} нет незакоммиченных изменений — нечего откатывать.",
            reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
        )
        return

    await query.edit_message_text(
        f"🗑️ Откатить ВСЕ незакоммиченные правки в {name}?\n"
        "Затронет только уже отслеживаемые git файлы (новые файлы не удалит). Необратимо.",
        reply_markup=InlineKeyboardMarkup(
            [confirm_row(f"proj:discard_yes:{project_id}", f"proj:manage:{project_id}", no_label="❌ Отмена")]
        ),
    )


def _discard_changes_blocking(project_id: int) -> str:
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return "Проект не найден."
        name = project.name

    path = project_local_path(project)
    if path is None:
        return f"⚠️ У {name} не задан local_path — нечего откатывать."
    ok, detail = discard_uncommitted_changes(path)
    log_action(str(project_id), "discard_uncommitted_changes", f"{name} ok={ok}")
    prefix = "✅" if ok else "❌"
    return f"{prefix} {name}: {detail}"


async def discard_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    await query.edit_message_text("⏳ Откатываю незакоммиченные правки…")
    text = await asyncio.to_thread(_discard_changes_blocking, project_id)
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")])
    )


async def prompt_delete_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Необратимо (проект просто пропадает из списка бота, хотя репозиторий
    на диске не трогается) — как и другие такие действия, требует
    подтверждения, а не срабатывает по одному тапу."""
    query = update.callback_query
    await query.answer()
    project_id = int(query.data.split(":")[-1])
    with get_session() as session:
        project = session.get(Project, project_id)
        name = project.name if project else "?"
    await query.edit_message_text(
        f"🗑️ Убрать «{name}» из списка бота? Репозиторий на диске это не удалит.",
        reply_markup=InlineKeyboardMarkup(
            [confirm_row(f"proj:del_yes:{project_id}", f"proj:manage:{project_id}", no_label="❌ Отмена")]
        ),
    )


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
    markup, _ = _management_menu(projects)
    await query.edit_message_text("📁 Проекты", reply_markup=markup)


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
        await query.edit_message_text(
            ADD_PROJECT_PROMPT,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
        )
        return

    rows = [
        [InlineKeyboardButton("📂 Выбрать локальный репозиторий", callback_data="proj:add:browse")],
        [InlineKeyboardButton("✍️ Ввести вручную", callback_data="proj:add:manual")],
        nav_row("menu:projects"),
    ]
    await query.edit_message_text("➕ Добавить проект — как?", reply_markup=InlineKeyboardMarkup(rows))


async def prompt_add_project_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting"] = "add_project"
    await query.edit_message_text(
        ADD_PROJECT_PROMPT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
    )


async def browse_local_repos(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    query = update.callback_query
    await query.answer()
    settings = context.application.bot_data["settings"]
    root = settings.local_repos_root
    if root is None:
        await query.edit_message_text(
            "LOCAL_REPOS_ROOT не задан в .env.", reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")])
        )
        return

    repos = discover_local_repos(root)
    if not repos:
        await query.edit_message_text(
            f"Не нашёл git-репозиториев в {root}.",
            reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
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
    page_rows, total_pages = paginate_rows(rows, page, nav_prefix="proj:add:browse:page")
    page_rows.append(nav_row("menu:projects"))
    page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""
    await query.edit_message_text(
        f"📂 Репозитории в {root}{page_note}:", reply_markup=InlineKeyboardMarkup(page_rows)
    )


async def browse_local_repos_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await browse_local_repos(update, context, page=page)


async def pick_local_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[-1])
    candidates = context.user_data.get("local_repo_candidates", [])
    if index >= len(candidates):
        await query.edit_message_text(
            "Список устарел, открой добавление проекта заново.",
            reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
        )
        return

    path = Path(candidates[index])
    name = path.name
    repo_full_name = detect_repo_full_name(path)

    if repo_full_name is None:
        context.user_data["pending_local_project"] = {"name": name, "local_path": str(path)}
        context.user_data["awaiting"] = "add_project_repo_name"
        # escape_markdown — имя папки/путь пришли с диска, не от нас: имя
        # вроде "my_project" (нечётное число "_") валит legacy Markdown-
        # парсер Telegram ("Can't find end of Italic entity"), а такие
        # имена — норма, не редкий edge case (см. PEP 8 snake_case).
        safe_name = escape_markdown(name, version=1)
        safe_path = escape_markdown(str(path), version=1)
        await query.edit_message_text(
            f"Не смог определить owner/repo из git remote для {safe_name} ({safe_path}).\n"
            "Отправь текстом `owner/repo`:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
        )
        return

    with get_session() as session:
        existing = session.scalar(select(Project).where(Project.repo_full_name == repo_full_name))
        if existing is not None:
            await query.edit_message_text(
                f"⚠️ Проект с repo {repo_full_name} уже есть в списке: {existing.name}.",
                reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
            )
            return
        session.add(Project(name=name, repo_full_name=repo_full_name, local_path=str(path)))
        session.commit()

    prefix = f"✅ Добавлено: {name} ({repo_full_name})\nlocal_path: {path}"
    # См. _reply_project_added — если добавление запрошено ИЗ визарда
    # выбора проектов, возвращаем на тот же экран с сохранённым выбором,
    # а не на самостоятельный список проектов.
    wizard = render_project_multiselect(context)
    if wizard is not None:
        wizard_text, wizard_markup = wizard
        await query.edit_message_text(f"{prefix}\n\n{wizard_text}", reply_markup=wizard_markup)
        return
    await query.edit_message_text(prefix, reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]))


async def _reply_project_added(update: Update, context: ContextTypes.DEFAULT_TYPE, prefix: str) -> None:
    """После успешного добавления проекта — если это было запрошено ИЗ
    визарда выбора проектов (кнопка "➕ Добавить проект" внутри
    мультивыбора ЧЕК/Фичи/Фикса/...), возвращаем на тот же экран с уже
    отмеченным выбором (flow['selected']), а не на самостоятельный экран
    📁 Проекты — раньше выбор терялся без возможности вернуться к нему,
    кроме перезапуска визарда с нуля (см. аудит меню)."""
    wizard = render_project_multiselect(context)
    if wizard is not None:
        wizard_text, wizard_markup = wizard
        await update.message.reply_text(f"{prefix}\n\n{wizard_text}", reply_markup=wizard_markup)
        return
    await update.message.reply_text(prefix, reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]))


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

    if awaiting and awaiting.startswith("last_prompt:"):
        project_id = int(awaiting.split(":", 1)[1])
        context.user_data["awaiting"] = None
        with get_session() as session:
            project = session.get(Project, project_id)
            path = project_local_path(project) if project else None
        if path is None:
            await update.message.reply_text(
                "Проект пропал или потерял local_path — не сохранил.",
                reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
            )
            return
        write_last_prompt(path, update.message.text)
        await update.message.reply_text(
            "✅ LAST_PROMPT.md обновлён.",
            reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
        )
        return

    if awaiting and awaiting.startswith("nightly_check_time:"):
        project_id = int(awaiting.split(":", 1)[1])
        raw = update.message.text.strip()
        try:
            datetime.strptime(raw, "%H:%M")
        except ValueError:
            await update.message.reply_text(
                "Не понял формат — отправь время как `HH:MM`, например `03:30`.",
                parse_mode="Markdown",
            )
            return
        context.user_data["awaiting"] = None
        with get_session() as session:
            project = session.get(Project, project_id)
            if project is None:
                await update.message.reply_text(
                    "Проект пропал — не сохранил.",
                    reply_markup=InlineKeyboardMarkup([nav_row("menu:projects")]),
                )
                return
            project.nightly_check_time = raw
            project.nightly_last_run_date = None
            session.commit()
        await update.message.reply_text(
            f"✅ Ночная проверка настроена на {raw} (по местному времени бота).",
            reply_markup=InlineKeyboardMarkup([nav_row(f"proj:manage:{project_id}")]),
        )
        return

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
        await _reply_project_added(update, context, text)
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
    await _reply_project_added(update, context, text)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(show_projects, pattern=r"^menu:projects$"))
    application.add_handler(CallbackQueryHandler(show_projects_page, pattern=r"^proj:page:\d+$"))
    application.add_handler(CallbackQueryHandler(prompt_add_project, pattern=r"^proj:add$"))
    application.add_handler(CallbackQueryHandler(prompt_add_project_manual, pattern=r"^proj:add:manual$"))
    application.add_handler(CallbackQueryHandler(browse_local_repos, pattern=r"^proj:add:browse$"))
    application.add_handler(
        CallbackQueryHandler(browse_local_repos_page, pattern=r"^proj:add:browse:page:\d+$")
    )
    application.add_handler(CallbackQueryHandler(pick_local_repo, pattern=r"^proj:add:pick:\d+$"))
    application.add_handler(CallbackQueryHandler(manage_project, pattern=r"^proj:manage:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_autocheck, pattern=r"^proj:toggle_auto:\d+$"))
    application.add_handler(CallbackQueryHandler(toggle_self_check, pattern=r"^proj:toggle_self:\d+$"))
    application.add_handler(CallbackQueryHandler(prompt_nightly_check_time, pattern=r"^proj:nightly:\d+$"))
    application.add_handler(
        CallbackQueryHandler(clear_nightly_check_time, pattern=r"^proj:nightly_clear:\d+$")
    )
    application.add_handler(CallbackQueryHandler(manual_push, pattern=r"^proj:push:\d+$"))
    application.add_handler(CallbackQueryHandler(prompt_discard_changes, pattern=r"^proj:discard:\d+$"))
    application.add_handler(CallbackQueryHandler(discard_changes, pattern=r"^proj:discard_yes:\d+$"))
    application.add_handler(CallbackQueryHandler(show_last_prompt, pattern=r"^proj:lastprompt:\d+$"))
    application.add_handler(CallbackQueryHandler(show_state_log, pattern=r"^proj:statelog:\d+$"))
    application.add_handler(
        CallbackQueryHandler(prompt_edit_last_prompt, pattern=r"^proj:lastprompt:edit:\d+$")
    )
    application.add_handler(CallbackQueryHandler(prompt_delete_project, pattern=r"^proj:del:\d+$"))
    application.add_handler(CallbackQueryHandler(delete_project, pattern=r"^proj:del_yes:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text), group=1)
