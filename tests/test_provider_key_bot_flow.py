"""⚙️ Настройки → 🔌 Провайдеры ИИ → 🔑 Ключ: задать API-ключ прямо из чата
вместо правки .env — тот же паттерн, что и у GitHub-токена (см.
test_github_token_bot_flow.py). Проверяет: ввод сохраняется и сразу
применяется к живому инстансу провайдера, сообщение с секретом чистится из
чата, чужой/пустой/с пробелом ввод не сохраняется, а "Убрать" возвращает
поведение к .env."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.settings_admin import (
    clear_provider_key,
    delete_extra_account,
    prompt_add_extra_account,
    prompt_set_provider_key,
    receive_provider_key_text,
)
from app.config import ProviderSettings
from app.db.models import ProviderName
from app.providers.accounts_store import add_extra_account, list_extra_accounts, list_extra_secrets
from app.providers.claude import ClaudeProvider
from app.providers.gemini import GeminiProvider
from app.providers.key_store import get_key_override
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _context(env_key: str | None = "env-key"):
    settings = SimpleNamespace(admin_tg_id=1, providers=ProviderSettings(gemini_api_key=env_key))
    gemini = GeminiProvider(env_key)
    registry = ProviderRegistry({ProviderName.GEMINI: gemini})
    return (
        SimpleNamespace(
            application=SimpleNamespace(bot_data={"settings": settings, "provider_registry": registry}),
            user_data={},
            bot=SimpleNamespace(send_message=AsyncMock()),
        ),
        gemini,
    )


def _callback_update(data: str, admin_tg_id: int = 1):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=admin_tg_id)), query


def _message_update(text: str, admin_tg_id: int = 1, chat_id: int = 1):
    message = SimpleNamespace(text=text, delete=AsyncMock())
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=admin_tg_id),
        effective_chat=SimpleNamespace(id=chat_id),
    )


def test_prompt_sets_awaiting_flag(db):
    update, query = _callback_update("set:key_set:gemini")
    context, _gemini = _context()

    _run(prompt_set_provider_key(update, context))

    assert context.user_data["awaiting"] == "provider_key:gemini"
    (text,), kwargs = query.edit_message_text.await_args
    assert "gemini" in text


def test_receive_key_saves_and_updates_live_provider(db):
    context, gemini = _context()
    context.user_data["awaiting"] = "provider_key:gemini"
    update = _message_update("new-secret-key")

    _run(receive_provider_key_text(update, context))

    assert get_key_override(ProviderName.GEMINI) == "new-secret-key"
    assert gemini._api_key == "new-secret-key"
    assert context.user_data["awaiting"] is None
    update.message.delete.assert_awaited_once()
    context.bot.send_message.assert_awaited_once()


def test_receive_key_deletes_message_even_though_it_held_the_secret(db):
    context, _gemini = _context()
    context.user_data["awaiting"] = "provider_key:gemini"
    update = _message_update("some-secret")

    _run(receive_provider_key_text(update, context))

    update.message.delete.assert_awaited_once()


def test_receive_key_ignores_update_when_not_awaiting(db):
    context, _gemini = _context()
    update = _message_update("should-be-ignored")

    _run(receive_provider_key_text(update, context))

    assert get_key_override(ProviderName.GEMINI) is None
    update.message.delete.assert_not_awaited()


def test_receive_key_ignores_unrelated_awaiting_value(db):
    context, gemini = _context()
    context.user_data["awaiting"] = "broadcast"
    update = _message_update("should-be-ignored")

    _run(receive_provider_key_text(update, context))

    assert get_key_override(ProviderName.GEMINI) is None
    assert gemini._api_key != "should-be-ignored"
    update.message.delete.assert_not_awaited()


def test_receive_key_rejects_text_with_whitespace(db):
    context, gemini = _context()
    context.user_data["awaiting"] = "provider_key:gemini"
    update = _message_update("this is not a key")

    _run(receive_provider_key_text(update, context))

    assert get_key_override(ProviderName.GEMINI) is None
    assert gemini._api_key != "this is not a key"
    context.bot.send_message.assert_awaited_once()
    (chat_id, text), kwargs = context.bot.send_message.await_args
    assert "не сохранён" in text


def test_receive_key_ignores_non_admin_even_if_flag_set(db):
    context, gemini = _context()
    context.user_data["awaiting"] = "provider_key:gemini"
    update = _message_update("stranger-key", admin_tg_id=999)

    _run(receive_provider_key_text(update, context))

    assert get_key_override(ProviderName.GEMINI) is None
    assert gemini._api_key != "stranger-key"


def test_clear_provider_key_reverts_to_env_value(db):
    from app.providers.key_store import set_key_override

    context, gemini = _context(env_key="env-key")
    set_key_override(ProviderName.GEMINI, "override-key")
    gemini.update_api_key("override-key")
    update, query = _callback_update("set:key_clear:gemini")

    _run(clear_provider_key(update, context))

    assert get_key_override(ProviderName.GEMINI) is None
    assert gemini._api_key == "env-key"
    query.answer.assert_awaited_once()


def test_claude_update_api_key_changes_primary_credential(db):
    claude = ClaudeProvider("old-key")

    claude.update_api_key("new-key")

    assert claude._api_key == "new-key"
    assert claude._all_credentials() == ["new-key"]


def test_claude_set_extra_accounts_appends_after_primary(db):
    claude = ClaudeProvider("primary-key")

    claude.set_extra_accounts(["second-key", "third-key"])

    assert claude._all_credentials() == ["primary-key", "second-key", "third-key"]


def test_prompt_add_extra_account_sets_awaiting_flag(db):
    update, query = _callback_update("set:key_add:gemini")
    context, _gemini = _context()

    _run(prompt_add_extra_account(update, context))

    assert context.user_data["awaiting"] == "provider_extra_key:gemini"
    (text,), kwargs = query.edit_message_text.await_args
    assert "gemini" in text


def test_receive_extra_key_adds_account_without_touching_primary(db):
    context, gemini = _context(env_key="primary-env-key")
    context.user_data["awaiting"] = "provider_extra_key:gemini"
    update = _message_update("second-account-key")

    _run(receive_provider_key_text(update, context))

    assert get_key_override(ProviderName.GEMINI) is None  # основной слот не тронут
    assert list_extra_secrets(ProviderName.GEMINI) == ["second-account-key"]
    assert gemini._extra_accounts == ["second-account-key"]
    assert gemini._all_credentials() == ["primary-env-key", "second-account-key"]
    update.message.delete.assert_awaited_once()


def test_receive_extra_key_appends_multiple_in_order(db):
    context, gemini = _context()
    context.user_data["awaiting"] = "provider_extra_key:gemini"
    _run(receive_provider_key_text(_message_update("acc-a"), context))
    context.user_data["awaiting"] = "provider_extra_key:gemini"
    _run(receive_provider_key_text(_message_update("acc-b"), context))

    assert list_extra_secrets(ProviderName.GEMINI) == ["acc-a", "acc-b"]
    assert gemini._extra_accounts == ["acc-a", "acc-b"]


def test_delete_extra_account_removes_and_updates_live_provider(db):
    context, gemini = _context()
    entry = add_extra_account(ProviderName.GEMINI, "to-remove")
    add_extra_account(ProviderName.GEMINI, "keep-me")
    gemini.set_extra_accounts(list_extra_secrets(ProviderName.GEMINI))
    update, query = _callback_update(f"set:key_del:gemini:{entry.id}")

    _run(delete_extra_account(update, context))

    assert [a.secret for a in list_extra_accounts(ProviderName.GEMINI)] == ["keep-me"]
    assert gemini._extra_accounts == ["keep-me"]
    query.answer.assert_awaited_once()
