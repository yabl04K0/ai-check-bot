"""🗨 Групповой ИИ-чат — свободная беседа с общим контекстом на несколько
ИИ-аккаунтов (см. app.ai_chat.orchestrator для самой логики хода). Один
активный чат на пользователя за раз, отслеживается через
context.user_data["awaiting"] == "ai_chat" + "ai_chat_session_id" — в
отличие от остальных "awaiting"-флагов в боте, этот НЕ одноразовый:
остаётся "ai_chat" между сообщениями, пока пользователь явно не закроет
чат кнопкой (см. app.bot.handlers.check.on_text и т.п. для контраста)."""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from app.ai_chat.approvals import DECISION_ALLOW, DECISION_ALWAYS, DECISION_DEFER, DECISION_DENY
from app.ai_chat.approvals import resolve as resolve_agent_approval
from app.ai_chat.orchestrator import run_turn
from app.ai_chat.sessions import (
    chat_belongs_to,
    close_chat_session,
    get_status,
    list_chat_sessions,
    recent_messages,
    reopen_chat_session,
)
from app.bot.keyboards import back_button, home_button, nav_row, paginate_rows
from app.db.models import AiChatSession
from app.db.session import get_session
from app.logging_setup import log_action
from app.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

FULL_ACCESS_DISCLAIMER = (
    "⚠️ Полный доступ к боту в этом чате\n\n"
    "Если согласишься, ИИ здесь сможет от твоего имени: запускать ЧЕК/Фичу/Фикс/"
    "Рефакторинг, менять приоритеты аккаунтов и режим делегации, смотреть статус "
    "провайдеров/прокси/задач. Прямого доступа к файлам/git/shell тут НЕТ — только "
    "эти явные действия через список инструментов. Задачи (ЧЕК/Фича/Фикс/...) "
    "запускаются СРАЗУ по слову ИИ, без обычного экрана подтверждения из меню.\n\n"
    "Без согласия чат всё равно работает — ИИ просто отвечает текстом и может "
    "делегировать под-вопросы другим аккаунтам, но не трогает бота."
)

CLOSE_CHAT_MARKUP = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🚪 Закрыть чат", callback_data="aichat:close"), home_button()]]
)


def reset_stale_chat(context: ContextTypes.DEFAULT_TYPE, actor_tg_id: int) -> None:
    """Закрывает "осиротевшую" сессию чата, если пользователь уходит с
    активного чата НЕ через "🚪 Закрыть чат" (🏠 Меню, повторный /start) —
    awaiting=="ai_chat" единственный НЕ одноразовый флаг в боте (см.
    докстринг модуля). Без этого AiChatSession оставалась бы "активной" в
    БД навсегда, а следующее свободное сообщение пользователя в ЛЮБОМ
    другом месте бота тихо перехватывалось бы этой старой сессией (см.
    аудит меню — при full_access это могло выполнить реальное действие по
    тексту, не предназначенному как команда чату)."""
    if context.user_data.get("awaiting") != "ai_chat":
        return
    session_id = context.user_data.get("ai_chat_session_id")
    if session_id is not None:
        close_chat_session(session_id)
        log_action(str(actor_tg_id), "ai_chat_closed", str(session_id))
    context.user_data["awaiting"] = None
    context.user_data["ai_chat_session_id"] = None


async def start_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, выдать полный доступ", callback_data="aichat:new:full")],
            [InlineKeyboardButton("💬 Нет, только чат", callback_data="aichat:new:limited")],
            [InlineKeyboardButton("📜 Мои чаты", callback_data="aichat:history")],
            [back_button()],
        ]
    )
    await query.edit_message_text(FULL_ACCESS_DISCLAIMER, reply_markup=markup)


async def create_ai_chat_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    full_access = query.data.endswith(":full")
    with get_session() as session:
        chat = AiChatSession(tg_user_id=str(update.effective_user.id), full_access=full_access)
        session.add(chat)
        session.flush()
        session_id = chat.id

    log_action(str(update.effective_user.id), "ai_chat_started", f"full_access={full_access}")
    context.user_data["awaiting"] = "ai_chat"
    context.user_data["ai_chat_session_id"] = session_id
    access_note = "с полным доступом к боту" if full_access else "без доступа к управлению ботом"
    await query.edit_message_text(
        f"🗨 Чат начат ({access_note}). Пиши сообщение — отвечу.",
        reply_markup=CLOSE_CHAT_MARKUP,
    )


_HISTORY_LABEL_LIMIT = 64  # лимит Telegram на текст inline-кнопки


async def show_chat_history(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    """📜 Мои чаты — список прошлых сессий (открытых и закрытых), тап по
    любой продолжает ИМЕННО её (та же история, не новый чат с нуля), см.
    resume_chat_session и запрос пользователя про повторное использование
    старого чата."""
    query = update.callback_query
    await query.answer()
    sessions = list_chat_sessions(str(update.effective_user.id))
    if not sessions:
        await query.edit_message_text(
            "У тебя пока нет ни одного чата.", reply_markup=InlineKeyboardMarkup([nav_row("menu:ai_chat")])
        )
        return

    rows = []
    for s in sessions:
        status = "🟢" if s.closed_at is None else "⚪"
        access = "🔓" if s.full_access else ""
        label = f"{status}{access} {s.created_at:%d.%m %H:%M} — {s.preview}"
        rows.append(
            [InlineKeyboardButton(label[:_HISTORY_LABEL_LIMIT], callback_data=f"aichat:resume:{s.id}")]
        )

    page_rows, total_pages = paginate_rows(rows, page, nav_prefix="aichat:hist:page")
    page_rows.append(nav_row("menu:ai_chat"))
    page_note = f" (стр. {page + 1}/{total_pages})" if total_pages > 1 else ""
    await query.edit_message_text(f"📜 Мои чаты{page_note}:", reply_markup=InlineKeyboardMarkup(page_rows))


async def show_chat_history_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    page = int(update.callback_query.data.split(":")[-1])
    await show_chat_history(update, context, page=page)


def _format_history_preview(messages) -> str:
    role_ru = {"user": "Ты", "assistant": "ИИ", "tool": "Действие"}
    lines = []
    for m in messages:
        role = role_ru.get(m.role, m.role)
        author_note = f" ({m.author})" if m.role == "assistant" and m.author else ""
        snippet = m.content if len(m.content) <= 200 else m.content[:200] + "…"
        lines.append(f"{role}{author_note}: {snippet}")
    return "\n\n".join(lines)


async def resume_chat_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    session_id = int(query.data.split(":")[-1])
    tg_user_id = str(update.effective_user.id)
    if not chat_belongs_to(session_id, tg_user_id):
        await query.answer("Чат не найден.", show_alert=True)
        return
    await query.answer()

    # Другой активный чат этого пользователя (если есть) закрываем перед
    # переключением — иначе он остался бы "активным" в БД молча, та же
    # логика, что при уходе через 🏠 Меню (см. reset_stale_chat).
    reset_stale_chat(context, update.effective_user.id)
    reopen_chat_session(session_id)
    context.user_data["awaiting"] = "ai_chat"
    context.user_data["ai_chat_session_id"] = session_id
    log_action(tg_user_id, "ai_chat_resumed", str(session_id))

    preview = _format_history_preview(recent_messages(session_id))
    text = f"🗨 Чат продолжен.\n\n{preview}\n\nПиши сообщение — отвечу." if preview else (
        "🗨 Чат продолжен (сообщений пока не было). Пиши сообщение — отвечу."
    )
    await query.edit_message_text(text[:4000], reply_markup=CLOSE_CHAT_MARKUP)


async def approve_native_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    token = query.data.split(":")[-1]
    resolve_agent_approval(token, DECISION_ALLOW)
    await query.answer("Разрешено")
    await query.edit_message_text("✅ Разрешено — агент запускается…")


async def reject_native_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    token = query.data.split(":")[-1]
    resolve_agent_approval(token, DECISION_DENY)
    await query.answer("Отклонено")
    await query.edit_message_text("❌ Отклонено — агент не запущен.")


async def always_allow_native_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    token = query.data.split(":")[-1]
    resolve_agent_approval(token, DECISION_ALWAYS)
    await query.answer("Разрешено (всегда)")
    await query.edit_message_text("♾️ Разрешено для этого проекта навсегда — агент запускается…")


async def defer_native_agent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    token = query.data.split(":")[-1]
    resolve_agent_approval(token, DECISION_DEFER)
    await query.answer("Отложено")
    await query.edit_message_text("⏸ Отложено — задачу можно перезапустить позже.")


async def close_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    reset_stale_chat(context, update.effective_user.id)
    await query.answer("Чат закрыт")
    await query.edit_message_text("🗨 Чат закрыт.")


async def receive_ai_chat_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "ai_chat":
        return
    session_id = context.user_data.get("ai_chat_session_id")
    if session_id is None:
        return

    registry: ProviderRegistry = context.application.bot_data["provider_registry"]
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    # create_task, не await напрямую — run_turn может пройти до
    # MAX_TOOL_STEPS кругов обращений к провайдеру (см.
    # app.ai_chat.orchestrator), а при дефолтном concurrent_updates=False
    # (см. app.bot.app.build_application) прямой await тут держал бы ВСЕ
    # следующие апдейты бота (не только этот чат — вообще любой тап любого
    # пользователя) до завершения хода. Тот же приём, что и запуск job'ы —
    # asyncio.create_task(start_job(...)) в check.py, не await.
    asyncio.create_task(_run_turn_and_reply(update, context, session_id, registry))


STATUS_POLL_SECONDS = 2


async def _poll_status(session_id: int, message) -> None:
    """Живо редактирует статус-сообщение, пока идёт run_turn — раньше
    единственной обратной связью на время всего хода (может пройти до
    MAX_TOOL_STEPS кругов делегирования/вызовов инструментов) был
    статичный индикатор "печатает…" без единого промежуточного сигнала
    (запрос пользователя: "улучши визуал выполнения всех команд"). Тот же
    приём, что app.bot.job_runner._progress_loop для job'ов."""
    last_text = None
    while True:
        await asyncio.sleep(STATUS_POLL_SECONDS)
        detail = get_status(session_id)
        text = f"⏳ {detail}" if detail else "⏳ Думаю…"
        if text != last_text:
            try:
                await message.edit_text(text)
                last_text = text
            except TelegramError:
                pass


async def _stop_status_poll(poll_task: asyncio.Task, status_message) -> None:
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass
    try:
        await status_message.delete()
    except TelegramError:
        pass


async def _run_turn_and_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session_id: int, registry: ProviderRegistry
) -> None:
    status_message = await update.message.reply_text("⏳ Думаю…")
    poll_task = asyncio.create_task(_poll_status(session_id, status_message))

    try:
        reply = await asyncio.to_thread(
            run_turn,
            session_id,
            update.message.text,
            registry=registry,
            application=context.application,
            tg_user_id=update.effective_user.id,
        )
    except Exception:  # noqa: BLE001 — один неудачный ход не должен закрывать чат насовсем
        logger.exception("Ошибка хода ИИ-чата #%s", session_id)
        await _stop_status_poll(poll_task, status_message)
        await update.message.reply_text(
            "⚠️ Не удалось получить ответ — чат остаётся открытым, попробуй ещё раз или закрой.",
            reply_markup=CLOSE_CHAT_MARKUP,
        )
        return

    await _stop_status_poll(poll_task, status_message)

    # Бьём на чанки по 3800 симв., как commit_show_diff в check.py —
    # reply[:4000] раньше молча терял хвост длинных ответов (особенно
    # после delegate) без единого следа для пользователя.
    chunks = [reply[i : i + 3800] for i in range(0, len(reply), 3800)] or [reply]
    for chunk in chunks[:-1]:
        await update.message.reply_text(chunk)
    await update.message.reply_text(chunks[-1], reply_markup=CLOSE_CHAT_MARKUP)


def register(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(start_ai_chat, pattern=r"^menu:ai_chat$"))
    application.add_handler(
        CallbackQueryHandler(create_ai_chat_session, pattern=r"^aichat:new:(full|limited)$")
    )
    application.add_handler(CallbackQueryHandler(show_chat_history, pattern=r"^aichat:history$"))
    application.add_handler(
        CallbackQueryHandler(show_chat_history_page, pattern=r"^aichat:hist:page:\d+$")
    )
    application.add_handler(CallbackQueryHandler(resume_chat_session, pattern=r"^aichat:resume:\d+$"))
    application.add_handler(
        CallbackQueryHandler(approve_native_agent, pattern=r"^aichat:agent_yes:\w+$")
    )
    application.add_handler(CallbackQueryHandler(reject_native_agent, pattern=r"^aichat:agent_no:\w+$"))
    application.add_handler(
        CallbackQueryHandler(always_allow_native_agent, pattern=r"^aichat:agent_always:\w+$")
    )
    application.add_handler(
        CallbackQueryHandler(defer_native_agent, pattern=r"^aichat:agent_defer:\w+$")
    )
    application.add_handler(CallbackQueryHandler(close_ai_chat, pattern=r"^aichat:close$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ai_chat_text), group=7)
