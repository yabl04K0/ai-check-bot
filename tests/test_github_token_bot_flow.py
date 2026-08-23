"""⚙️ Настройки → 🐙 GitHub → 🔑 Токен: задать токен прямо из чата вместо
правки .env. Проверяет, что: ввод сохраняется и сразу используется
CursorProvider'ом, сообщение с секретом чистится из чата, чужой/пустой
ввод не сохраняется, а "Убрать" возвращает поведение к .env."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.github import (
    clear_token,
    prompt_set_token,
    receive_token_text,
)
from app.db.models import ProviderName
from app.github_integration.token_store import get_token_override, set_token_override
from app.providers.cursor import CursorProvider
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _context(admin_tg_id: int = 1, env_token: str | None = "ghp_env_token"):
    settings = SimpleNamespace(admin_tg_id=admin_tg_id, github_token=env_token)
    cursor = CursorProvider("cursor-agent", github_token=env_token)
    registry = ProviderRegistry({ProviderName.CURSOR: cursor})
    return (
        SimpleNamespace(
            application=SimpleNamespace(
                bot_data={"settings": settings, "provider_registry": registry}
            ),
            user_data={},
            bot=SimpleNamespace(send_message=AsyncMock()),
        ),
        cursor,
    )


def _callback_update(admin_tg_id: int = 1):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id)), query


def _message_update(text: str, admin_tg_id: int = 1, chat_id: int = 1):
    message = SimpleNamespace(text=text, delete=AsyncMock())
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=admin_tg_id),
        effective_chat=SimpleNamespace(id=chat_id),
    )


def test_prompt_sets_awaiting_flag(db):
    update, query = _callback_update()
    context, _cursor = _context()

    _run(prompt_set_token(update, context))

    assert context.user_data["awaiting"] == "github_token"
    (text,), kwargs = query.edit_message_text.await_args
    assert "GitHub-токен" in text


def test_receive_token_saves_and_updates_live_cursor_provider(db):
    context, cursor = _context()
    context.user_data["awaiting"] = "github_token"
    update = _message_update("ghp_new_secret_token")

    _run(receive_token_text(update, context))

    assert get_token_override() == "ghp_new_secret_token"
    assert cursor._github_token == "ghp_new_secret_token"
    assert context.user_data["awaiting"] is None
    update.message.delete.assert_awaited_once()
    context.bot.send_message.assert_awaited_once()


def test_receive_token_deletes_message_even_though_it_held_the_secret(db):
    context, _cursor = _context()
    context.user_data["awaiting"] = "github_token"
    update = _message_update("ghp_secret")

    _run(receive_token_text(update, context))

    update.message.delete.assert_awaited_once()


def test_receive_token_ignores_update_when_not_awaiting(db):
    context, _cursor = _context()
    update = _message_update("ghp_should_be_ignored")

    _run(receive_token_text(update, context))

    assert get_token_override() is None
    update.message.delete.assert_not_awaited()


def test_receive_token_rejects_text_with_whitespace(db):
    context, cursor = _context()
    context.user_data["awaiting"] = "github_token"
    update = _message_update("this is not a token")

    _run(receive_token_text(update, context))

    assert get_token_override() is None
    assert cursor._github_token != "this is not a token"
    context.bot.send_message.assert_awaited_once()
    (chat_id, text), kwargs = context.bot.send_message.await_args
    assert "не сохранён" in text


def test_receive_token_ignores_non_admin_even_if_flag_set(db):
    context, cursor = _context(admin_tg_id=1)
    context.user_data["awaiting"] = "github_token"
    update = _message_update("ghp_from_stranger", admin_tg_id=999)

    _run(receive_token_text(update, context))

    assert get_token_override() is None
    assert cursor._github_token != "ghp_from_stranger"


def test_clear_token_reverts_cursor_provider_to_env_value(db):
    context, cursor = _context(env_token="ghp_env_token")
    set_token_override("ghp_override")
    cursor.update_github_token("ghp_override")
    update, query = _callback_update()

    _run(clear_token(update, context))

    assert get_token_override() is None
    assert cursor._github_token == "ghp_env_token"
    query.answer.assert_awaited_once()
