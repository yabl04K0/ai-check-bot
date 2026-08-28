"""Один ход 🗨 Группового ИИ-чата — общая история (AiChatMessage) на всю
беседу, оркестратор (аккаунт тира "Глава", если делегация включена и
назначена, иначе первый подключённый провайдер) может делегировать
под-вопрос аккаунту любого тира (см. app.providers.tiers.call_tier_account
— это и есть "система агентов и делегации", доступна ВСЕГДА, это просто
ещё один собеседник, не "управление ботом") и, если пользователь выдал
полный доступ этому чату (AiChatSession.full_access), вызывать
инструменты бота (см. app.ai_chat.tools).

Протокол вызова — простой текстовый маркер, не нативный function-calling
провайдера (единообразно работает через ЛЮБОГО из них, тот же приём, что
app.tasks.findings_parse.parse_structured_findings): чтобы вызвать
действие, ВЕСЬ ответ ИИ должен быть ровно одной строкой
    ДЕЙСТВИЕ: имя | арг1=значение1; арг2=значение2
иначе весь ответ уходит пользователю как обычный текст."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.ai_chat.sessions import set_status
from app.ai_chat.tools import TOOLS, ToolContext
from app.db.models import AccountPriority, AiChatMessage, AiChatSession, ProviderName
from app.db.session import get_session
from app.providers.base import ProviderError, RunOptions
from app.providers.prompt_augment import PromptAugmentProvider
from app.providers.registry import ProviderRegistry
from app.providers.tiers import TierPicker, call_tier_account, delegation_mode_enabled

MAX_TOOL_STEPS = 6
_ACTION_RE = re.compile(r"^\s*ДЕЙСТВИЕ:\s*(\S+)\s*(?:\|\s*(.*))?\s*$", re.IGNORECASE | re.DOTALL)

_ROLE_RU = {"user": "Пользователь", "assistant": "Ты", "tool": "Результат действия"}


def _parse_args(raw: str | None) -> dict[str, str]:
    args: dict[str, str] = {}
    if not raw:
        return args
    for part in raw.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep:
            args[key.strip()] = value.strip()
    return args


def _system_prompt(full_access: bool) -> str:
    lines = [
        "Ты участвуешь в общем ИИ-чате пользователя ai-check-bot — в одном "
        "разговоре могут отвечать РАЗНЫЕ ИИ-аккаунты по очереди, история общая.",
        "",
        "Можешь делегировать под-вопрос аккаунту другого тира — ответь РОВНО "
        "одной строкой (без пояснений до/после):",
        "ДЕЙСТВИЕ: delegate | tier=head|medium|delegation; prompt=текст под-вопроса",
        "Результат придёт следующим сообщением с пометкой 'Результат действия' — "
        "тогда сформулируй обычный текстовый ответ пользователю.",
        "Если delegate ответит, что в тире нет аккаунтов, а тебе нужно НЕ просто "
        "текстовый ответ, а реально что-то СДЕЛАТЬ в коде проекта (поправить файл, "
        "выполнить команду) — используй run_native_agent (если он тебе доступен ниже) "
        "вместо повторных попыток delegate.",
    ]
    if full_access:
        lines.append("")
        lines.append(
            "Пользователь выдал этому чату полный доступ к управлению ботом. "
            "Доступные инструменты (вызов — та же форма, ДЕЙСТВИЕ: имя | аргументы):"
        )
        for name, spec in TOOLS.items():
            lines.append(f"- {name}: {spec.description}")
    else:
        lines.append("")
        lines.append(
            "Доступа к управлению ботом у тебя в этом чате нет — только делегирование "
            "(см. выше) и обычный текстовый ответ."
        )
    lines.append("")
    lines.append(
        "Если не вызываешь ДЕЙСТВИЕ — просто ответь пользователю обычным текстом, "
        "он уйдёт ему напрямую."
    )
    return "\n".join(lines)


def _history_prompt(messages: list[AiChatMessage]) -> str:
    parts = []
    for m in messages:
        role = _ROLE_RU.get(m.role, m.role)
        author_note = f" ({m.author})" if m.role == "assistant" and m.author else ""
        parts.append(f"{role}{author_note}: {m.content}")
    return "\n\n".join(parts)


def _pick_orchestrator(registry: ProviderRegistry) -> tuple[ProviderName, str | None]:
    if delegation_mode_enabled():
        account = TierPicker().pick(AccountPriority.HEAD)
        if account is not None and not registry.is_disabled(account.provider):
            return account.provider, account.account_label
    connected = registry.connected()
    if not connected:
        raise ProviderError("Нет ни одного подключённого провайдера для чата.")
    return connected[0], None


def _run_delegate(registry: ProviderRegistry, args: dict[str, str]) -> str:
    tier_raw = args.get("tier", "").strip().lower()
    sub_prompt = args.get("prompt", "").strip()
    if not sub_prompt:
        return "delegate: не передан prompt."
    try:
        priority = AccountPriority(tier_raw)
    except ValueError:
        return f"delegate: неизвестный тир '{tier_raw}' (head/medium/delegation)."
    outcome = call_tier_account(priority, registry, sub_prompt)
    if outcome is None:
        return f"delegate: нет доступных аккаунтов в тире {tier_raw}."
    account, result = outcome
    return f"[{account.provider.value}:{account.account_label}] {result.text}"


def _describe_action(action_name: str, args: dict[str, str]) -> str:
    """Текст статус-сообщения на время выполнения одного действия — см.
    app.ai_chat.sessions.set_status/app.bot.handlers.ai_chat (запрос
    пользователя: "улучши визуал выполнения всех команд" — раньше вся
    цепочка ДЕЙСТВИЕ→результат→снова ДЕЙСТВИЕ была полностью невидима
    пользователю, единственная обратная связь — статичный "печатает…")."""
    if action_name == "delegate":
        tier = args.get("tier", "?")
        return f"🤝 Делегирую под-вопрос в тир «{tier}»…"
    if action_name == "run_native_agent":
        project = args.get("project", "?")
        return f"🤖 Запускаю настоящего агента на проекте «{project}» (может занять до 30 минут)…"
    return f"🔧 Выполняю: {action_name}…"


def run_turn(
    session_id: int, user_text: str, *, registry: ProviderRegistry, application, tg_user_id: int
) -> str:
    from app.ai_chat.compact import maybe_compact

    try:
        set_status(session_id, "🗜 Сжимаю историю чата…")
        maybe_compact(session_id, registry=registry, application=application, tg_user_id=tg_user_id)
    finally:
        set_status(session_id, None)

    with get_session() as db:
        chat = db.get(AiChatSession, session_id)
        full_access = chat.full_access if chat else False
        db.add(AiChatMessage(session_id=session_id, role="user", content=user_text))

    tool_ctx = ToolContext(
        registry=registry, application=application, tg_user_id=tg_user_id, session_id=session_id
    )

    try:
        for step in range(MAX_TOOL_STEPS):
            with get_session() as db:
                history = list(
                    db.scalars(
                        select(AiChatMessage)
                        .where(AiChatMessage.session_id == session_id)
                        .order_by(AiChatMessage.id)
                    ).all()
                )

            try:
                provider_name, forced_label = _pick_orchestrator(registry)
            except ProviderError as exc:
                return f"⚠️ {exc}"

            account_note = f"{provider_name.value}:{forced_label or 'primary'}"
            thinking_note = "думает" if step == 0 else "формулирует ответ по результату действия"
            set_status(session_id, f"🧠 {account_note} {thinking_note}…")

            provider = PromptAugmentProvider(registry.get(provider_name), force_limits=True)
            options = RunOptions(system=_system_prompt(full_access), forced_account_label=forced_label)
            try:
                result = provider.run_prompt(_history_prompt(history), options)
            except ProviderError as exc:
                return f"⚠️ Ошибка ИИ: {exc}"

            text = result.text.strip()
            author = account_note
            match = _ACTION_RE.match(text)

            with get_session() as db:
                db.add(AiChatMessage(session_id=session_id, role="assistant", author=author, content=text))

            if not match:
                return text

            action_name = match.group(1).strip().lower()
            args = _parse_args(match.group(2))
            set_status(session_id, _describe_action(action_name, args))

            if action_name == "delegate":
                tool_result = _run_delegate(registry, args)
            elif action_name not in TOOLS:
                tool_result = f"Неизвестное действие: {action_name}"
            elif not full_access:
                tool_result = "Инструменты бота недоступны — в этом чате не выдан полный доступ."
            else:
                try:
                    tool_result = TOOLS[action_name].handler(tool_ctx, args)
                except Exception as exc:  # noqa: BLE001 — инструмент не должен ронять весь чат
                    tool_result = f"Ошибка инструмента '{action_name}': {exc}"

            with get_session() as db:
                db.add(AiChatMessage(session_id=session_id, role="tool", content=tool_result))

        return "⚠️ Слишком много действий подряд без финального ответа — прерываю, уточни вопрос."
    finally:
        set_status(session_id, None)
