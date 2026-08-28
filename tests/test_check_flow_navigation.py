"""Визард ЧЕК/Фича/Фикс — навигация "Назад" не должна сбрасывать
выбранные проекты (см. app/bot/handlers/check.py::back_to_projects,
back_to_scope), и 🔧 Фикс всё требует подтверждения перед запуском
(report_fix_all/_yes/_no), как commit_ask/commit_yes/commit_no."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import check as check_module
from app.db.models import AccountPriority, Job, Project, ProviderName, TaskType
from app.db.session import get_session
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _update_and_context(callback_data: str, *, flow=None, user_data=None, registry=None):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=callback_data)
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1))
    data = user_data or {}
    if flow is not None:
        data["flow"] = flow
    context = SimpleNamespace(
        user_data=data,
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(bot_data={"provider_registry": registry or ProviderRegistry({})}),
    )
    return update, context, edit


def test_back_to_projects_preserves_selection(db):
    with get_session() as session:
        session.add(Project(name="P1", repo_full_name="o/p1"))
        session.add(Project(name="P2", repo_full_name="o/p2"))
        session.flush()
        p1_id = session.query(Project).filter_by(name="P1").one().id

    flow = {"task_type": TaskType.CHECK_FULL, "selected": {p1_id}, "scope": None, "comment": None}
    update, context, edit = _update_and_context(
        "chk:back:projects", flow=flow, user_data={"awaiting": "comment"}
    )

    _run(check_module.back_to_projects(update, context))

    assert context.user_data["awaiting"] is None
    args, kwargs = edit.await_args
    markup = kwargs["reply_markup"]
    checked_labels = [row[0].text for row in markup.inline_keyboard if row[0].text.startswith("☑️")]
    assert any("P1" in label for label in checked_labels)
    assert not any("P2" in label for label in checked_labels)


def test_back_to_projects_without_active_flow_shows_alert(db):
    update, context, edit = _update_and_context("chk:back:projects", flow=None)

    _run(check_module.back_to_projects(update, context))

    edit.assert_not_awaited()
    # Telegram отвергает повторный answer() на один callback — раньше
    # тут отвечали дважды (безусловный answer() в начале функции + этот),
    # из-за чего алерт "сессия устарела" реально никогда не показывался.
    assert update.callback_query.answer.call_count == 1
    update.callback_query.answer.assert_awaited_with(
        "Сессия выбора устарела, начни заново из меню.", show_alert=True
    )


def test_toggle_project_stale_session_answers_exactly_once(db):
    update, context, edit = _update_and_context("chk:proj:1", flow=None)

    _run(check_module.toggle_project(update, context))

    edit.assert_not_awaited()
    assert update.callback_query.answer.call_count == 1
    update.callback_query.answer.assert_awaited_with(
        "Сессия выбора устарела, начни заново из меню.", show_alert=True
    )


def test_back_to_scope_shows_scope_menu(db):
    update, context, edit = _update_and_context("chk:back:scope", user_data={"awaiting": "comment"})

    _run(check_module.back_to_scope(update, context))

    assert context.user_data["awaiting"] is None
    args, kwargs = edit.await_args
    assert args[0] == "Скоуп?"


def test_confirm_menu_gives_way_back_instead_of_dead_end_cancel():
    """Раньше confirm_menu — последний шаг визарда — был единственным
    экраном без пути назад: только "✖ Отмена" → menu:main, стиравшая весь
    прогресс (см. аудит меню). Теперь nav_row даёт и Назад (сохраняет
    прогресс, теперь ведёт на экран "🤖 ИИ для задачи" — новый предпоследний
    шаг визарда), и Меню (полный сброс, кто раньше был "Отмена")."""
    from app.bot.keyboards import confirm_menu

    markup = confirm_menu(TaskType.CHECK_FULL)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "chk:back:ai" in callbacks
    assert "menu:main" in callbacks


def test_skip_comment_shows_ai_picker_not_confirm(db):
    """Комментарий необязателен -> "Пропустить" ведёт на "🤖 ИИ для задачи"
    (новый шаг визарда), не сразу на подтверждение."""
    flow = {"task_type": TaskType.CHECK_FULL, "selected": {1}, "scope": "all", "comment": None}
    update, context, edit = _update_and_context("chk:comment:skip", flow=flow)

    _run(check_module.skip_comment(update, context))

    args, kwargs = edit.await_args
    assert "ИИ для этой задачи" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "chk:ai:next" in callbacks
    assert "chk:back:comment" in callbacks


def test_cycle_flow_tier_cycles_and_stores_in_flow(db, monkeypatch):
    """Тап по аккаунту крутит его приоритет по кругу в flow['tier_overrides']
    — тот же цикл, что у глобальных Настроек (не задан → Глава → Средний →
    Делегация → не задан), но БЕЗ обращения к БД: оверрайд живёт только в
    flow до confirm()."""
    monkeypatch.setattr(
        check_module,
        "all_known_accounts",
        lambda registry: [
            SimpleNamespace(provider=ProviderName.GEMINI, account_label="primary"),
        ],
    )
    flow = {"task_type": TaskType.CHECK_FULL, "selected": {1}, "scope": "all", "comment": None}
    update, context, edit = _update_and_context("chk:ai:cycle:gemini:primary", flow=flow)

    _run(check_module.cycle_flow_tier(update, context))
    assert flow["tier_overrides"] == {"gemini:primary": AccountPriority.HEAD}

    _run(check_module.cycle_flow_tier(update, context))
    assert flow["tier_overrides"] == {"gemini:primary": AccountPriority.MEDIUM}

    _run(check_module.cycle_flow_tier(update, context))
    assert flow["tier_overrides"] == {"gemini:primary": AccountPriority.DELEGATION}

    _run(check_module.cycle_flow_tier(update, context))
    assert flow["tier_overrides"] == {}  # четвёртый тап снимает приоритет


def test_back_to_ai_shows_ai_picker_screen(db):
    flow = {
        "task_type": TaskType.CHECK_FULL,
        "selected": {1},
        "scope": "all",
        "comment": "x",
        "tier_overrides": {},
    }
    update, context, edit = _update_and_context("chk:back:ai", flow=flow)

    _run(check_module.back_to_ai(update, context))

    args, kwargs = edit.await_args
    assert "ИИ для этой задачи" in args[0]


def test_back_to_ai_stale_flow_shows_alert(db):
    update, context, edit = _update_and_context("chk:back:ai", flow=None)

    _run(check_module.back_to_ai(update, context))

    edit.assert_not_awaited()
    assert update.callback_query.answer.call_count == 1


def test_confirm_summary_shows_default_ai_note_when_no_overrides(db):
    flow = {"task_type": TaskType.CHECK_FULL, "selected": {1}, "scope": "all", "comment": None}
    summary = check_module._build_confirm_summary(flow)
    assert "ИИ для задачи: настройки по умолчанию" in summary


def test_confirm_summary_shows_chosen_ai_overrides(db):
    flow = {
        "task_type": TaskType.CHECK_FULL,
        "selected": {1},
        "scope": "all",
        "comment": None,
        "tier_overrides": {"gemini:primary": AccountPriority.HEAD},
    }
    summary = check_module._build_confirm_summary(flow)
    assert "ИИ для задачи: 👑 gemini:primary" in summary


def test_back_to_comment_for_check_type_goes_to_comment_menu_with_scope_back(db):
    flow = {"task_type": TaskType.CHECK_FULL, "selected": {1}, "scope": "all", "comment": None}
    update, context, edit = _update_and_context("chk:back:comment", flow=flow)

    _run(check_module.back_to_comment(update, context))

    assert context.user_data["awaiting"] == "comment"
    args, kwargs = edit.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "chk:back:scope" in callbacks


def test_back_to_comment_for_non_check_type_goes_to_projects_back(db):
    flow = {"task_type": TaskType.FIX, "selected": {1}, "scope": None, "comment": None}
    update, context, edit = _update_and_context("chk:back:comment", flow=flow)

    _run(check_module.back_to_comment(update, context))

    assert context.user_data["awaiting"] == "comment"
    args, kwargs = edit.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "chk:back:projects" in callbacks


def test_back_to_comment_stale_flow_shows_alert(db):
    update, context, edit = _update_and_context("chk:back:comment", flow=None)

    _run(check_module.back_to_comment(update, context))

    edit.assert_not_awaited()
    assert update.callback_query.answer.call_count == 1


def test_confirm_summary_shows_scope_button_label_not_raw_key(db):
    """Раньше сводка перед подтверждением показывала сырой internal-ключ
    ('all_ignore_registry') вместо текста кнопки, которую нажал
    пользователь (см. аудит меню)."""
    flow = {
        "task_type": TaskType.CHECK_FULL,
        "selected": {1},
        "scope": "all_ignore_registry",
        "comment": None,
    }
    summary = check_module._build_confirm_summary(flow)
    assert "ЧЕК всё (игнор отложенного)" in summary
    assert "all_ignore_registry" not in summary


def _make_report_job(session, task_type=TaskType.CHECK_FULL) -> int:
    project = Project(name="P", repo_full_name="o/p")
    session.add(project)
    session.flush()
    job = Job(task_type=task_type, progress_total=1)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job.id


def test_report_fix_all_asks_for_confirmation_first(db):
    with get_session() as session:
        job_id = _make_report_job(session)

    update, context, edit = _update_and_context(f"report:fix_all:{job_id}")

    _run(check_module.report_fix_all(update, context))

    args, kwargs = edit.await_args
    assert "?" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"report:fix_all_yes:{job_id}" in callbacks
    assert f"report:fix_all_no:{job_id}" in callbacks


def test_report_fix_all_yes_enqueues_fix_job(db, monkeypatch):
    with get_session() as session:
        job_id = _make_report_job(session)

    monkeypatch.setattr(check_module, "start_job", AsyncMock())
    update, context, edit = _update_and_context(f"report:fix_all_yes:{job_id}")

    _run(check_module.report_fix_all_yes(update, context))

    with get_session() as session:
        fix_jobs = session.query(Job).filter_by(task_type=TaskType.FIX).all()
        assert len(fix_jobs) == 1


def test_report_fix_all_no_returns_to_report_menu_without_enqueueing(db):
    with get_session() as session:
        job_id = _make_report_job(session)

    update, context, edit = _update_and_context(f"report:fix_all_no:{job_id}")

    _run(check_module.report_fix_all_no(update, context))

    with get_session() as session:
        assert session.query(Job).filter_by(task_type=TaskType.FIX).count() == 0
    args, kwargs = edit.await_args
    assert f"#{job_id}" in args[0]
