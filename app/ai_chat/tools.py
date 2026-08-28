"""Инструменты бота, доступные ИИ в 🗨 Групповом чате — явный allowlist
(list_projects/start_check/set_tier и т.п.), НИКАКОГО сырого доступа к
процессу/файловой системе/git (это прерогатива CLI-агентов вроде
Cursor/Codex под их СОБСТВЕННЫМ тумблером GITHUB_TOKEN, см.
app.providers.ai_autonomy — тут не дублируем чужую ответственность).

Список активен только когда пользователь явно выдал полный доступ
ИМЕННО этому чату (см. запрос пользователя: "перед входом в такой чат
спрашивать выдавать ли все права", app.db.models.AiChatSession.full_access)
— при отказе ИИ в чате отвечает только текстом (делегирование другим
тирам через app.providers.tiers.call_tier_account остаётся доступно
всегда, это не "управление ботом", а просто ещё один собеседник).

Текстовый протокол вызова (не нативный function-calling — единообразно
работает через ЛЮБОЙ провайдер, тот же приём, что и
app.tasks.findings_parse.parse_structured_findings) реализован в
app.ai_chat.orchestrator, этот модуль — только сам список инструментов
и их исполнение."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.ai_chat import agent_activity
from app.ai_chat.approvals import (
    DECISION_ALWAYS,
    DECISION_DEFER,
    DECISION_DENY,
    create_pending,
    wait_for_decision,
)
from app.ai_chat.sessions import recent_messages
from app.db.models import AccountPriority, Job, Project, ProviderName, TaskType
from app.db.session import get_session
from app.providers.agent_permissions import (
    can_edit_code,
    native_agent_always_allowed,
    set_native_agent_always_allowed,
)
from app.providers.ai_autonomy import ai_command_auto_approve_enabled, ai_native_agents_enabled
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.providers.registry import ProviderRegistry
from app.providers.tiers import all_tier_assignments, delegation_mode_enabled, set_delegation_mode, set_tier
from app.tasks.project_context import local_path as project_local_path
from app.tasks.queue import JobQueue
from app.tasks.types import TASK_TYPE_LABELS
from app.tasks.web_research import fetch_url as _fetch_url
from app.tasks.web_research import web_search as _web_search

_GENERIC_TASK_TYPES = {
    "feature": TaskType.FEATURE,
    "fix": TaskType.FIX,
    "refactor": TaskType.REFACTOR,
    "custom": TaskType.CUSTOM,
}


@dataclass
class ToolContext:
    registry: ProviderRegistry
    application: object  # telegram.ext.Application — типизировать нельзя, тут не бот-слой
    tg_user_id: int
    session_id: int | None = None


@dataclass(frozen=True)
class ToolSpec:
    description: str
    handler: Callable[[ToolContext, dict], str]


def _find_project(session, name: str) -> Project | None:
    return session.scalar(select(Project).where(Project.name == name))


_ROLE_RU = {"user": "Пользователь", "assistant": "Ты", "tool": "Результат действия"}


def _recent_chat_context(session_id: int | None, *, limit: int = 8) -> str:
    if session_id is None:
        return ""
    messages = recent_messages(session_id, limit=limit)
    if not messages:
        return ""
    parts = []
    for m in messages:
        role = _ROLE_RU.get(m.role, m.role)
        author_note = f" ({m.author})" if m.role == "assistant" and m.author else ""
        parts.append(f"{role}{author_note}: {m.content}")
    return "\n\n".join(parts)


def _tool_list_projects(ctx: ToolContext, args: dict[str, str]) -> str:
    with get_session() as session:
        projects = session.scalars(select(Project)).all()
    if not projects:
        return "Проектов нет."
    return "\n".join(f"- {p.name} ({p.repo_full_name})" for p in projects)


def _tool_list_providers(ctx: ToolContext, args: dict[str, str]) -> str:
    lines = []
    for name, provider in ctx.registry.all().items():
        status = provider.auth_status()
        disabled = " [отключён]" if ctx.registry.is_disabled(name) else ""
        lines.append(f"- {name.value}: {status.status.value}{disabled}")
    return "\n".join(lines)


def _tool_list_tiers(ctx: ToolContext, args: dict[str, str]) -> str:
    lines = [f"Режим делегации: {'включён' if delegation_mode_enabled() else 'выключен'}"]
    assignments = all_tier_assignments()
    if not assignments:
        lines.append("Тиры не назначены ни одному аккаунту.")
    for account, tier in assignments.items():
        lines.append(f"- {account.provider.value}:{account.account_label} — {tier.value}")
    return "\n".join(lines)


def _tool_set_tier(ctx: ToolContext, args: dict[str, str]) -> str:
    provider_raw = args.get("provider", "").strip()
    account_label = args.get("account_label", "").strip()
    tier_raw = args.get("tier", "").strip().lower()
    if not provider_raw or not account_label:
        return "set_tier: нужны provider и account_label."
    try:
        provider_name = ProviderName(provider_raw)
    except ValueError:
        return f"set_tier: неизвестный провайдер '{provider_raw}'."
    priority = None
    if tier_raw not in ("", "none", "нет", "не задан"):
        try:
            priority = AccountPriority(tier_raw)
        except ValueError:
            return f"set_tier: неизвестный тир '{tier_raw}' (head/medium/delegation/none)."
    set_tier(provider_name, account_label, priority)
    return f"Ок: {provider_raw}:{account_label} -> {priority.value if priority else 'не задан'}"


def _tool_toggle_delegation(ctx: ToolContext, args: dict[str, str]) -> str:
    flag = args.get("enabled", "").strip().lower() in ("true", "1", "да", "on", "вкл", "включить")
    set_delegation_mode(flag)
    return f"Режим делегации: {'включён' if flag else 'выключен'}"


def _tool_start_check(ctx: ToolContext, args: dict[str, str]) -> str:
    from app.bot.job_runner import start_job

    project_name = args.get("project", "").strip()
    mode = args.get("mode", "full").strip().lower()
    task_type = TaskType.CHECK_LITE if mode == "lite" else TaskType.CHECK_FULL
    with get_session() as session:
        project = _find_project(session, project_name)
        if project is None:
            return f"start_check: проект '{project_name}' не найден."
        job = JobQueue(session).enqueue(task_type, [project.id], created_by_tg_id=ctx.tg_user_id)
        job_id = job.id
    asyncio.create_task(start_job(ctx.application, job_id))
    return f"Запущен job #{job_id} ({task_type.value}) для {project_name}."


def _tool_start_task(ctx: ToolContext, args: dict[str, str]) -> str:
    from app.bot.job_runner import start_job

    project_name = args.get("project", "").strip()
    task_type_raw = args.get("type", "").strip().lower()
    comment = args.get("comment", "").strip() or None
    task_type = _GENERIC_TASK_TYPES.get(task_type_raw)
    if task_type is None:
        return f"start_task: неизвестный type '{task_type_raw}' (feature/fix/refactor/custom)."
    with get_session() as session:
        project = _find_project(session, project_name)
        if project is None:
            return f"start_task: проект '{project_name}' не найден."
        job = JobQueue(session).enqueue(
            task_type, [project.id], comment=comment, created_by_tg_id=ctx.tg_user_id
        )
        job_id = job.id
    asyncio.create_task(start_job(ctx.application, job_id))
    return f"Запущен job #{job_id} ({TASK_TYPE_LABELS[task_type]}) для {project_name}."


def _tool_list_recent_jobs(ctx: ToolContext, args: dict[str, str]) -> str:
    limit = 10
    with get_session() as session:
        jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
    if not jobs:
        return "Задач ещё не было."
    return "\n".join(f"#{j.id} {j.task_type.value} — {j.status.value}" for j in jobs)


def _tool_list_proxies(ctx: ToolContext, args: dict[str, str]) -> str:
    from app.db.models import ProxyPoolEntry, ProxyPoolStatus

    with get_session() as session:
        pool = session.scalars(select(ProxyPoolEntry)).all()
    active = sum(1 for p in pool if p.status == ProxyPoolStatus.ACTIVE)
    dead = sum(1 for p in pool if p.status == ProxyPoolStatus.DEAD)
    return f"Пул: {len(pool)}, активных: {active}, мёртвых: {dead}."


def _await_agent_approval(ctx: ToolContext, project_name: str, task: str) -> str | None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    token = create_pending()
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Разрешить", callback_data=f"aichat:agent_yes:{token}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"aichat:agent_no:{token}"),
            ],
            [
                InlineKeyboardButton(
                    "♾️ Всегда для проекта", callback_data=f"aichat:agent_always:{token}"
                ),
                InlineKeyboardButton("⏸ Отложить", callback_data=f"aichat:agent_defer:{token}"),
            ],
        ]
    )
    text = (
        f"🤖 ИИ просит запустить настоящего агента на проекте «{project_name}»:\n\n{task}\n\n"
        "У агента будет реальный доступ к файлам/bash в этом проекте. Разрешить?"
    )
    asyncio.run(ctx.application.bot.send_message(ctx.tg_user_id, text, reply_markup=markup))
    return wait_for_decision(token)


def _tool_run_native_agent(ctx: ToolContext, args: dict[str, str]) -> str:
    if not ai_native_agents_enabled():
        return (
            "run_native_agent: выключено. Включи в ⚙️ Настройки → доступ ИИ, чтобы разрешить "
            "запуск настоящих агентов Claude Code (реальный доступ к файлам/bash в проекте)."
        )

    project_name = args.get("project", "").strip()
    task = args.get("task", "").strip()
    if not project_name or not task:
        return "run_native_agent: нужны project и task."

    with get_session() as session:
        project = _find_project(session, project_name)
        if project is None:
            return f"run_native_agent: проект '{project_name}' не найден."
        path = project_local_path(project)
    if path is None:
        return f"run_native_agent: у проекта '{project_name}' нет доступного local_path."

    try:
        provider = ctx.registry.get(ProviderName.CLAUDE_CODE)
    except KeyError:
        return "run_native_agent: провайдер claude_code не настроен."
    if not isinstance(provider, ClaudeCodeCliProvider):
        return "run_native_agent: провайдер claude_code недоступен."

    if not ai_command_auto_approve_enabled() and not native_agent_always_allowed(project_name):
        decision = _await_agent_approval(ctx, project_name, task)
        if decision is None:
            return "run_native_agent: не дождался подтверждения (истекло время ожидания) — агент не запущен."
        if decision == DECISION_DENY:
            return "run_native_agent: отклонено пользователем — агент не запущен."
        if decision == DECISION_DEFER:
            return "run_native_agent: отложено пользователем — задачу можно перезапустить позже."
        if decision == DECISION_ALWAYS:
            set_native_agent_always_allowed(project_name, True)

    context_block = _recent_chat_context(ctx.session_id)
    full_task = f"Контекст из чата:\n{context_block}\n\nЗадача: {task}" if context_block else task

    activity_id = agent_activity.start(project_name, task)
    try:
        result = provider.run_agentic_task(
            full_task, str(path), can_edit=can_edit_code(ProviderName.CLAUDE_CODE)
        )
    finally:
        agent_activity.finish(activity_id)
    return result.text


def _tool_send_message(ctx: ToolContext, args: dict[str, str]) -> str:
    text = args.get("text", "").strip()
    if not text:
        return "send_message: нужен text."
    asyncio.run(ctx.application.bot.send_message(ctx.tg_user_id, text[:4000]))
    return "Отправлено."


def _tool_send_file(ctx: ToolContext, args: dict[str, str]) -> str:
    project_name = args.get("project", "").strip()
    rel_path = args.get("path", "").strip()
    caption = args.get("caption", "").strip() or None
    if not project_name or not rel_path:
        return "send_file: нужны project и path."

    with get_session() as session:
        project = _find_project(session, project_name)
        if project is None:
            return f"send_file: проект '{project_name}' не найден."
        root = project_local_path(project)
    if root is None:
        return f"send_file: у проекта '{project_name}' нет доступного local_path."

    root = Path(root).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents and target != root:
        return "send_file: путь выходит за пределы проекта — отказано."
    if not target.is_file():
        return f"send_file: файл '{rel_path}' не найден в проекте."

    asyncio.run(
        ctx.application.bot.send_document(
            ctx.tg_user_id, document=target.read_bytes(), filename=target.name, caption=caption
        )
    )
    return f"Файл '{rel_path}' отправлен."


def _tool_web_search(ctx: ToolContext, args: dict[str, str]) -> str:
    query = args.get("query", "").strip()
    if not query:
        return "web_search: нужен query."
    results = _web_search(query)
    if not results:
        return "Ничего не найдено (или поиск недоступен прямо сейчас)."
    lines = [f"{i}. {r.title}\n{r.url}\n{r.snippet}" for i, r in enumerate(results, start=1)]
    return "\n\n".join(lines)


def _tool_fetch_url(ctx: ToolContext, args: dict[str, str]) -> str:
    url = args.get("url", "").strip()
    if not url:
        return "fetch_url: нужен url."
    return _fetch_url(url)


TOOLS: dict[str, ToolSpec] = {
    "list_projects": ToolSpec("список зарегистрированных проектов", _tool_list_projects),
    "list_providers": ToolSpec("статус подключения каждого ИИ-провайдера", _tool_list_providers),
    "list_tiers": ToolSpec(
        "текущие приоритеты аккаунтов и статус режима делегации", _tool_list_tiers
    ),
    "set_tier": ToolSpec(
        "задать тир аккаунту: provider=...; account_label=primary|extra:N; "
        "tier=head|medium|delegation|none",
        _tool_set_tier,
    ),
    "toggle_delegation": ToolSpec(
        "включить/выключить режим делегации: enabled=true|false", _tool_toggle_delegation
    ),
    "start_check": ToolSpec(
        "запустить ЧЕК: project=...; mode=full|lite (по умолчанию full)", _tool_start_check
    ),
    "start_task": ToolSpec(
        "запустить задачу: project=...; type=feature|fix|refactor|custom; comment=...",
        _tool_start_task,
    ),
    "list_recent_jobs": ToolSpec("последние 10 задач и их статус", _tool_list_recent_jobs),
    "list_proxies": ToolSpec("сводка по пулу прокси (активные/мёртвые)", _tool_list_proxies),
    "run_native_agent": ToolSpec(
        "запустить НАСТОЯЩЕГО агента Claude Code на своей же подписке — реальный доступ "
        "к файлам/bash в проекте (не просто текстовый ответ): project=...; task=что сделать. "
        "Требует отдельного включения в Настройках; используй, если для задачи нужно реально "
        "поменять код/выполнить команды, а не просто получить текст.",
        _tool_run_native_agent,
    ),
    "send_message": ToolSpec(
        "отправить пользователю отдельное сообщение прямо сейчас (не финальный ответ хода): text=...",
        _tool_send_message,
    ),
    "send_file": ToolSpec(
        "отправить пользователю файл из проекта: project=...; path=относительный путь в проекте; "
        "caption=подпись (необязательно)",
        _tool_send_file,
    ),
    "web_search": ToolSpec(
        "поискать в интернете (DuckDuckGo, без ключа): query=что искать — вернёт заголовки/ссылки/сниппеты",
        _tool_web_search,
    ),
    "fetch_url": ToolSpec(
        "скачать страницу и вернуть её текст (HTML очищается от тегов): url=...",
        _tool_fetch_url,
    ),
}
