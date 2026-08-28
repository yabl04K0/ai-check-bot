"""Фиксы из аудита меню для app/bot/handlers/github.py:
1. Список репозиториев (show_github_menu) теперь пагинируется по 8, как
   остальные крупные списки бота — раньше был единственным без пагинации.
2. Экран "GitHub-токен не задан" (show_github_menu/close_public без
   клиента) ведёт "Назад" на menu:main, а не сам на себя (menu:github) —
   раньше это был тупик без единого выхода инлайн-кнопками.
3. confirm_close_public оформлен через confirm_row (единый стиль
   подтверждения), а не ручной сборкой рядов."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers import github as github_module


def _run(coro):
    return asyncio.run(coro)


class _Repo:
    def __init__(self, full_name: str, private: bool = False, open_issues: int = 0):
        self.full_name = full_name
        self.private = private
        self.open_issues = open_issues


def _update_and_context(callback_data: str, repos, monkeypatch, *, has_client: bool = True):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=callback_data)
    update = SimpleNamespace(callback_query=query)
    settings = SimpleNamespace(admin_tg_id=1, github_token=None)
    context = SimpleNamespace(user_data={}, application=SimpleNamespace(bot_data={"settings": settings}))

    if has_client:
        client = MagicMock()
        client.list_repos.return_value = repos
        monkeypatch.setattr(github_module, "_get_client", lambda ctx: client)
    else:
        monkeypatch.setattr(github_module, "_get_client", lambda ctx: None)

    fake_age = SimpleNamespace(days_since=1, needs_rotation_warning=False)
    monkeypatch.setattr(github_module, "check_token_age", lambda session, token: fake_age)
    return update, context, edit


def test_show_github_menu_paginates_beyond_page_size(monkeypatch, db):
    repos = [_Repo(f"owner/repo{i}") for i in range(12)]
    update, context, edit = _update_and_context("menu:github", repos, monkeypatch)

    _run(github_module.show_github_menu(update, context))

    args, kwargs = edit.await_args
    assert "стр. 1/2" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "gh:page:1" in callbacks


def test_show_github_menu_second_page(monkeypatch, db):
    repos = [_Repo(f"owner/repo{i}") for i in range(12)]
    update, context, edit = _update_and_context("gh:page:1", repos, monkeypatch)

    _run(github_module.show_github_menu_page(update, context))

    args, kwargs = edit.await_args
    assert "стр. 2/2" in args[0]


def test_show_github_menu_empty_repos_shows_explanatory_header(monkeypatch, db):
    update, context, edit = _update_and_context("menu:github", [], monkeypatch)

    _run(github_module.show_github_menu(update, context))

    args, kwargs = edit.await_args
    assert "не найдены" in args[0]


def test_show_github_menu_no_token_back_goes_to_main_not_itself(monkeypatch):
    update, context, edit = _update_and_context("menu:github", [], monkeypatch, has_client=False)

    _run(github_module.show_github_menu(update, context))

    args, kwargs = edit.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "menu:main" in callbacks
    assert "menu:github" not in callbacks


def test_confirm_close_public_uses_confirm_row_style(monkeypatch):
    update, context, edit = _update_and_context("gh:close_public", [], monkeypatch)

    _run(github_module.confirm_close_public(update, context))

    args, kwargs = edit.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "gh:close_public_confirm" in callbacks
    assert "menu:github" in callbacks
