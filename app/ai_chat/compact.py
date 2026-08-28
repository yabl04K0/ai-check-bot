from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, select

from app.db.models import AiChatMessage
from app.db.session import get_session
from app.providers.base import ProviderError, RunOptions
from app.providers.prompt_augment import PromptAugmentProvider
from app.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

COMPACT_THRESHOLD_CHARS = 600_000
KEEP_RECENT_MESSAGES = 12
COMPACT_AUTHOR = "система: сжатие контекста"

_ROLE_RU = {"user": "Пользователь", "assistant": "Ты", "tool": "Результат действия"}

_SUMMARY_SYSTEM = (
    "Сожми следующую историю переписки в плотный фактический пересказ: что "
    "обсуждалось, какие решения приняты, какие вопросы остались открытыми, "
    "что понадобится для продолжения разговора. Без вступлений и оценок, "
    "только факты, максимально сжато."
)


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def _compact_prompt(messages: list[AiChatMessage]) -> str:
    parts = []
    for m in messages:
        role = _ROLE_RU.get(m.role, m.role)
        author_note = f" ({m.author})" if m.role == "assistant" and m.author else ""
        parts.append(f"{role}{author_note}: {m.content}")
    return "\n\n".join(parts)


def maybe_compact(session_id: int, *, registry: ProviderRegistry, application, tg_user_id: int) -> bool:
    from app.ai_chat.orchestrator import _pick_orchestrator

    with get_session() as db:
        messages = list(
            db.scalars(
                select(AiChatMessage)
                .where(AiChatMessage.session_id == session_id)
                .order_by(AiChatMessage.id)
            ).all()
        )

    if len(messages) <= KEEP_RECENT_MESSAGES:
        return False

    total_chars = sum(len(m.content) for m in messages)
    if total_chars < COMPACT_THRESHOLD_CHARS:
        return False

    to_compact = messages[:-KEEP_RECENT_MESSAGES]

    try:
        provider_name, forced_label = _pick_orchestrator(registry)
        provider = PromptAugmentProvider(registry.get(provider_name))
        options = RunOptions(system=_SUMMARY_SYSTEM, forced_account_label=forced_label)
        result = provider.run_prompt(_compact_prompt(to_compact), options)
    except ProviderError:
        logger.exception("Не удалось сжать историю чата #%s", session_id)
        return False

    summary_text = "[Сжатая история более раннего разговора]\n\n" + result.text.strip()
    original_count = len(to_compact)
    approx_tokens = sum(estimate_tokens(m.content) for m in to_compact)
    first_id = to_compact[0].id
    rest_ids = [m.id for m in to_compact[1:]]

    with get_session() as db:
        first_message = db.get(AiChatMessage, first_id)
        if first_message is None:
            return False
        first_message.role = "assistant"
        first_message.author = COMPACT_AUTHOR
        first_message.content = summary_text
        if rest_ids:
            db.execute(delete(AiChatMessage).where(AiChatMessage.id.in_(rest_ids)))

    notification = (
        f"🗜 Контекст чата сжат — было {original_count} сообщений "
        f"(~{approx_tokens} токенов), стало кратким пересказом. Продолжаю."
    )
    asyncio.run(application.bot.send_message(tg_user_id, notification))
    return True
