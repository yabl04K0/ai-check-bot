"""Жизненный цикл 🗨 Группового ИИ-чата как сущности — список прошлых
чатов и переоткрытие старого вместо обязательного старта с нуля (см.
запрос пользователя: "сделай схему чатов что бы можно было использовать
старый чат снова в будущем"). Сама логика ХОДА (run_turn) — в
app.ai_chat.orchestrator, инструменты бота — в app.ai_chat.tools; тут
только сама сессия и её история как данные.

closed_at не "удаляет" чат — только помечает его неактивным для
receive_ai_chat_text. reopen_chat_session просто снимает эту пометку:
вся прежняя история (AiChatMessage) остаётся на месте и продолжает расти
в ТОЙ ЖЕ сессии, а не создаётся заново."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import AiChatMessage, AiChatSession
from app.db.session import get_session

PREVIEW_LENGTH = 40


@dataclass(frozen=True)
class ChatSessionSummary:
    id: int
    created_at: datetime
    closed_at: datetime | None
    full_access: bool
    preview: str


def close_chat_session(session_id: int) -> None:
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        if chat is not None:
            chat.closed_at = datetime.now(timezone.utc)


def reopen_chat_session(session_id: int) -> None:
    """tg_user_id/full_access не трогаем — согласие на полный доступ,
    данное при старте ЭТОГО чата, остаётся в силе для него же."""
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        if chat is not None:
            chat.closed_at = None


def chat_belongs_to(session_id: int, tg_user_id: str) -> bool:
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        return chat is not None and chat.tg_user_id == tg_user_id


def list_chat_sessions(tg_user_id: str, *, limit: int = 50) -> list[ChatSessionSummary]:
    with get_session() as session:
        chats = session.scalars(
            select(AiChatSession)
            .where(AiChatSession.tg_user_id == tg_user_id)
            .order_by(AiChatSession.created_at.desc())
            .limit(limit)
        ).all()
        summaries = []
        for chat in chats:
            first_user_message = session.scalar(
                select(AiChatMessage)
                .where(AiChatMessage.session_id == chat.id, AiChatMessage.role == "user")
                .order_by(AiChatMessage.id)
                .limit(1)
            )
            preview = first_user_message.content[:PREVIEW_LENGTH] if first_user_message else "(пусто)"
            summaries.append(
                ChatSessionSummary(
                    id=chat.id,
                    created_at=chat.created_at,
                    closed_at=chat.closed_at,
                    full_access=chat.full_access,
                    preview=preview,
                )
            )
        return summaries


def set_status(session_id: int, detail: str | None) -> None:
    """Что оркестратор делает ПРЯМО СЕЙЧАС в рамках текущего хода — см.
    app.ai_chat.orchestrator.run_turn (вызывается из фонового потока,
    отдельная короткая сессия, тот же приём, что и
    app.providers.note_tracking.NoteTrackingProvider) и опрашивается
    поллинг-циклом в app.bot.handlers.ai_chat для живого статус-сообщения
    (запрос пользователя: "улучши визуал выполнения всех команд")."""
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        if chat is not None:
            chat.status_detail = detail


def get_status(session_id: int) -> str | None:
    with get_session() as session:
        chat = session.get(AiChatSession, session_id)
        return chat.status_detail if chat is not None else None


def sessions_with_live_status() -> list[AiChatSession]:
    with get_session() as session:
        chats = session.scalars(
            select(AiChatSession).where(
                AiChatSession.closed_at.is_(None), AiChatSession.status_detail.is_not(None)
            )
        ).all()
        session.expunge_all()
        return list(chats)


def recent_messages(session_id: int, *, limit: int = 6) -> list[AiChatMessage]:
    with get_session() as session:
        rows = list(
            session.scalars(
                select(AiChatMessage)
                .where(AiChatMessage.session_id == session_id)
                .order_by(AiChatMessage.id.desc())
                .limit(limit)
            ).all()
        )
        session.expunge_all()
        return list(reversed(rows))
