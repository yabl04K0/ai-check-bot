"""📁 Проекты → ➕ Добавить: без LOCAL_REPOS_ROOT поведение не меняется
(сразу ручной ввод) — с ним появляется выбор, а сам пик автоматически
определяет owner/repo из git remote, либо просит ввести его текстом,
если remote не распознан (не GitHub / нет remote)."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.projects import browse_local_repos, on_text, pick_local_repo, prompt_add_project
from app.db.models import Project
from app.db.session import get_session


def _run(coro):
    return asyncio.run(coro)


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _update_and_context(local_repos_root=None, user_data=None):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="proj:add")
    update = SimpleNamespace(callback_query=query)
    settings = SimpleNamespace(local_repos_root=local_repos_root)
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"settings": settings}), user_data=user_data or {}
    )
    return update, context, edit


def test_prompt_add_project_skips_menu_when_root_not_configured(db):
    update, context, edit = _update_and_context(local_repos_root=None)

    _run(prompt_add_project(update, context))

    assert context.user_data["awaiting"] == "add_project"


def test_prompt_add_project_shows_choice_when_root_configured(tmp_path, db):
    update, context, edit = _update_and_context(local_repos_root=tmp_path)

    _run(prompt_add_project(update, context))

    assert "awaiting" not in context.user_data
    args, kwargs = edit.await_args
    assert "как" in args[0].lower()


def test_browse_lists_discovered_repos(tmp_path, db):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    update, context, edit = _update_and_context(local_repos_root=tmp_path)
    update.callback_query.data = "proj:add:browse"

    _run(browse_local_repos(update, context))

    assert context.user_data["local_repo_candidates"] == [str(repo)]


def test_pick_local_repo_creates_project_when_remote_detected(tmp_path, db):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("remote", "add", "origin", "https://github.com/owner/myrepo.git", cwd=repo)

    update, context, edit = _update_and_context(
        local_repos_root=tmp_path, user_data={"local_repo_candidates": [str(repo)]}
    )
    update.callback_query.data = "proj:add:pick:0"

    _run(pick_local_repo(update, context))

    with get_session() as session:
        project = session.query(Project).filter_by(repo_full_name="owner/myrepo").one()
        assert project.local_path == str(repo)
        assert project.name == "myrepo"


def test_pick_local_repo_asks_for_manual_repo_name_when_remote_unknown(tmp_path, db):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)  # без remote

    update, context, edit = _update_and_context(
        local_repos_root=tmp_path, user_data={"local_repo_candidates": [str(repo)]}
    )
    update.callback_query.data = "proj:add:pick:0"

    _run(pick_local_repo(update, context))

    assert context.user_data["awaiting"] == "add_project_repo_name"
    assert context.user_data["pending_local_project"] == {"name": "myrepo", "local_path": str(repo)}

    with get_session() as session:
        assert session.query(Project).count() == 0


def test_on_text_completes_pending_local_project_with_typed_repo_name(tmp_path, db):
    reply = AsyncMock()
    message = SimpleNamespace(text="owner/typed-repo", reply_text=reply)
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(
        user_data={
            "awaiting": "add_project_repo_name",
            "pending_local_project": {"name": "myrepo", "local_path": str(tmp_path / "myrepo")},
        }
    )

    _run(on_text(update, context))

    with get_session() as session:
        project = session.query(Project).filter_by(repo_full_name="owner/typed-repo").one()
        assert project.name == "myrepo"
        assert project.local_path == str(tmp_path / "myrepo")
    assert context.user_data["awaiting"] is None


def test_pick_local_repo_rejects_stale_index(tmp_path, db):
    update, context, edit = _update_and_context(
        local_repos_root=tmp_path, user_data={"local_repo_candidates": []}
    )
    update.callback_query.data = "proj:add:pick:0"

    _run(pick_local_repo(update, context))

    args, kwargs = edit.await_args
    assert "устарел" in args[0]
