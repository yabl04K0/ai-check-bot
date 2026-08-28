"""⚙️ Настройки → 🔌 Провайдеры ИИ → 🔑 Ключ → 🧠 Сменить модель (см.
app/bot/handlers/settings_admin.py::prompt_set_provider_model/receive_provider_model_text)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import settings_admin as settings_module
from app.db.models import ProviderName
from app.providers.gemini import GeminiProvider
from app.providers.model_store import get_model_override, set_model_override
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _registry():
    return ProviderRegistry({ProviderName.GEMINI: GeminiProvider("api-key")})


def _context(registry):
    settings = SimpleNamespace(admin_tg_id=1)
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"settings": settings, "provider_registry": registry}),
        user_data={},
    )


def test_prompt_set_provider_model_sets_awaiting_and_shows_current():
    registry = _registry()
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="set:model_set:gemini")
    update = SimpleNamespace(callback_query=query)
    context = _context(registry)

    _run(settings_module.prompt_set_provider_model(update, context))

    assert context.user_data["awaiting"] == "provider_model:gemini"
    args, kwargs = edit.await_args
    assert "gemini-" in args[0] or "models/" in args[0] or "Текущая модель" in args[0]


def test_prompt_set_provider_model_escapes_unsafe_markdown_in_current_name(db):
    """current — свободный текст, ранее сохранённый пользователем через
    receive_provider_model_text (только проверка "без пробелов", не
    markdown-безопасность). Имя вроде "my_model" (нечётное число "_")
    валит legacy Markdown-парсер Telegram без экранирования — тот же
    класс бага, что уже чинили для имён репозиториев (см. аудит меню)."""
    registry = _registry()
    set_model_override(ProviderName.GEMINI, "my_unsafe_model")
    registry.get(ProviderName.GEMINI).update_model("my_unsafe_model")
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="set:model_set:gemini")
    update = SimpleNamespace(callback_query=query)
    context = _context(registry)

    _run(settings_module.prompt_set_provider_model(update, context))

    args, kwargs = edit.await_args
    assert r"my\_unsafe\_model" in args[0]


def test_receive_provider_model_text_updates_live_provider_and_persists(db):
    registry = _registry()
    reply = AsyncMock()
    message = SimpleNamespace(text="llama-3.1-8b-instant", reply_text=reply)
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    context = _context(registry)
    context.user_data["awaiting"] = "provider_model:gemini"

    _run(settings_module.receive_provider_model_text(update, context))

    assert context.user_data["awaiting"] is None
    assert registry.get(ProviderName.GEMINI).current_model == "llama-3.1-8b-instant"
    assert get_model_override(ProviderName.GEMINI) == "llama-3.1-8b-instant"
    reply.assert_awaited_once()


def test_receive_provider_model_text_rejects_text_with_spaces(db):
    registry = _registry()
    reply = AsyncMock()
    message = SimpleNamespace(text="not a model name", reply_text=reply)
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    context = _context(registry)
    context.user_data["awaiting"] = "provider_model:gemini"

    _run(settings_module.receive_provider_model_text(update, context))

    assert get_model_override(ProviderName.GEMINI) is None
    args, kwargs = reply.await_args
    assert "не тот текст" in args[0]


def test_receive_provider_model_text_ignores_when_not_awaiting(db):
    registry = _registry()
    reply = AsyncMock()
    message = SimpleNamespace(text="llama-3.1-8b-instant", reply_text=reply)
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1))
    context = _context(registry)
    context.user_data["awaiting"] = None

    _run(settings_module.receive_provider_model_text(update, context))

    reply.assert_not_awaited()
