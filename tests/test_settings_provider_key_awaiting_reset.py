"""show_provider_key — стандартная точка посадки после ◀️ Назад со всех
awaiting-экранов провайдера (ключ/модель/доп.аккаунт). Раньше не сбрасывал
awaiting: уход через "Назад" (не через явный сброс) оставлял его висеть,
и следующее произвольное сообщение пользователя в ЛЮБОМ другом месте
бота тихо сохранялось бы как новый API-ключ этого провайдера (см. аудит
меню)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers import settings_admin as settings_module
from app.db.models import ProviderName
from app.providers.gemini import GeminiProvider
from app.providers.registry import ProviderRegistry


def _run(coro):
    return asyncio.run(coro)


def _context(user_data=None):
    registry = ProviderRegistry({ProviderName.GEMINI: GeminiProvider("api-key")})
    settings = SimpleNamespace(admin_tg_id=1)
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"settings": settings, "provider_registry": registry}),
        user_data=user_data if user_data is not None else {},
    )


def test_show_provider_key_resets_stale_awaiting(db):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data="set:key:gemini")
    update = SimpleNamespace(callback_query=query)
    context = _context(user_data={"awaiting": "provider_key:gemini"})

    _run(settings_module.show_provider_key(update, context))

    assert context.user_data.get("awaiting") is None
