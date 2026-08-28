"""🐙 GitHub → Открытые issues — постраничный список вместо жёсткого
среза на 20 без индикатора (см. app/bot/handlers/github.py::show_issues)."""

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


def _update_and_context(callback_data: str, repos, issues, monkeypatch):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=callback_data)
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        user_data={"gh_repos": {github_module._repo_key(r.full_name): r for r in repos}},
        application=SimpleNamespace(bot_data={"settings": SimpleNamespace()}),
    )
    client = MagicMock()
    client.list_issues.return_value = issues
    monkeypatch.setattr(github_module, "_get_client", lambda ctx: client)
    return update, context, edit


def _issues(n: int):
    return [{"number": i, "title": f"issue {i}"} for i in range(n)]


def test_show_issues_paginates_beyond_page_size(monkeypatch):
    repo = _Repo("owner/repo")
    key = github_module._repo_key(repo.full_name)
    update, context, edit = _update_and_context(f"gh:issues:{key}", [repo], _issues(12), monkeypatch)

    _run(github_module.show_issues(update, context))

    args, kwargs = edit.await_args
    assert "(12)" in args[0]
    assert "стр. 1/2" in args[0]
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"gh:issues:{key}:1" in callbacks


def test_show_issues_second_page(monkeypatch):
    repo = _Repo("owner/repo")
    key = github_module._repo_key(repo.full_name)
    update, context, edit = _update_and_context(f"gh:issues:{key}:1", [repo], _issues(12), monkeypatch)

    _run(github_module.show_issues(update, context))

    args, kwargs = edit.await_args
    assert "стр. 2/2" in args[0]
    assert "issue 8" in args[0]


def test_show_issues_no_pagination_widget_when_short(monkeypatch):
    repo = _Repo("owner/repo")
    key = github_module._repo_key(repo.full_name)
    update, context, edit = _update_and_context(f"gh:issues:{key}", [repo], _issues(3), monkeypatch)

    _run(github_module.show_issues(update, context))

    args, kwargs = edit.await_args
    assert "стр." not in args[0]


def test_show_issues_back_button_returns_to_repo_card_not_full_list(monkeypatch):
    """Раньше "Назад" на экране issues вёл сразу на menu:github (полный
    список репо), перепрыгивая через карточку конкретного репо, с которой
    пользователь реально сюда зашёл (см. аудит меню)."""
    repo = _Repo("owner/repo")
    key = github_module._repo_key(repo.full_name)
    update, context, edit = _update_and_context(f"gh:issues:{key}", [repo], _issues(3), monkeypatch)

    _run(github_module.show_issues(update, context))

    args, kwargs = edit.await_args
    callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"gh:repo:{key}" in callbacks
    assert "menu:github" not in callbacks


def test_show_issues_stale_key_after_repo_removed(monkeypatch):
    """Ключ по хэшу full_name — а не по позиции в списке — значит тап по
    кнопке остаётся валидным, даже если GitHub вернул репозитории в другом
    порядке между рендером списка и тапом; невалиден он, только если
    репозиторий реально пропал из последнего gh_repos."""
    update, context, edit = _update_and_context("gh:issues:deadbeefcafe", [], _issues(1), monkeypatch)

    _run(github_module.show_issues(update, context))

    update.callback_query.answer.assert_awaited_with("Список устарел.", show_alert=True)
    # Telegram отвергает повторный answer() на один callback — раньше тут
    # был безусловный answer() в начале функции + этот, из-за чего алерт
    # "список устарел" реально никогда не показывался.
    assert update.callback_query.answer.call_count == 1
    edit.assert_not_awaited()
