"""Режим 'Авто' — роутер выбирает провайдера под задачу.

Правила (см. README, "Выбор провайдера под задачу"):
- решает по типу задачи, доступной квоте каждого подключенного провайдера,
  истории успешности на похожих задачах;
- при недоступности провайдера — fallback-цепочка (обобщение текущих
  Tier 1/2/3 из DELEGATION.md на любой провайдер).
"""

from __future__ import annotations

from app.db.models import ProviderName, TaskType
from app.providers.base import QuotaEstimate
from app.providers.registry import ProviderRegistry

# Хвост фолбэка, общий почти для всех типов задач: провайдеры, добавленные
# позже через общий OpenAI-совместимый контракт (см.
# app.providers.openai_compatible) — не первый выбор ни для одной задачи
# по умолчанию, но лучше доехать на них, чем не доехать вообще, если
# основная тройка (Claude/Codex/Cursor) недоступна или на пределе квоты.
_EXTRA_FALLBACK = [
    ProviderName.GEMINI,
    ProviderName.DEEPSEEK,
    ProviderName.GROK,
    ProviderName.MISTRAL,
    ProviderName.OPENROUTER,
    ProviderName.TOGETHER,
    ProviderName.PERPLEXITY,
    ProviderName.FIREWORKS,
]

# Приоритет провайдеров по умолчанию для каждого типа задачи (первый — предпочтительный).
# Чек (Full) — штатный флот-протокол заточен под Claude; Lite — легче,
# локалка и Groq (LPU, очень низкая задержка) как scout вытягивают первыми,
# чтобы экономить платную квоту тяжёлых провайдеров.
# Claude Code CLI (подписка Max/Pro, флэт-рейт, может держать несколько
# аккаунтов — см. app.providers.claude_code_cli) — перед метрируемым CLAUDE
# (ANTHROPIC_API_KEY): дешевле гонять на подписке, пока не исчерпана/не
# подключена, и только потом платить за токены.
DEFAULT_PRIORITY: dict[TaskType, list[ProviderName]] = {
    TaskType.CHECK_FULL: [
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CODEX,
        ProviderName.CURSOR,
        *_EXTRA_FALLBACK,
    ],
    TaskType.CHECK_LITE: [
        ProviderName.LOCAL_LLM,
        ProviderName.GROQ,
        ProviderName.CEREBRAS,
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CODEX,
        *_EXTRA_FALLBACK,
    ],
    TaskType.FEATURE: [
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CURSOR,
        ProviderName.CODEX,
        *_EXTRA_FALLBACK,
    ],
    TaskType.FIX: [
        ProviderName.CURSOR,
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CODEX,
        *_EXTRA_FALLBACK,
    ],
    TaskType.REFACTOR: [
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CURSOR,
        ProviderName.CODEX,
        *_EXTRA_FALLBACK,
    ],
    TaskType.CUSTOM: [
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CODEX,
        ProviderName.CURSOR,
        ProviderName.LOCAL_LLM,
        ProviderName.GROQ,
        ProviderName.CEREBRAS,
        *_EXTRA_FALLBACK,
    ],
}

# Порог, выше которого провайдер считается "почти без квоты" и роутер
# пропускает его в пользу следующего в цепочке (если оценка вообще есть —
# см. QuotaEstimate.is_estimate, официального учёта у провайдеров нет).
QUOTA_SKIP_THRESHOLD_PCT = 95.0


class NoProviderAvailableError(RuntimeError):
    """Ни один провайдер из цепочки не подключен/не доступен по квоте."""


def _is_quota_exhausted(estimate: QuotaEstimate) -> bool:
    return estimate.used_pct is not None and estimate.used_pct >= QUOTA_SKIP_THRESHOLD_PCT


def pick_provider(
    task_type: TaskType,
    registry: ProviderRegistry,
    *,
    success_scores: dict[ProviderName, float] | None = None,
    priority_override: list[ProviderName] | None = None,
) -> ProviderName:
    """Возвращает имя выбранного провайдера или кидает NoProviderAvailableError.

    success_scores — опциональная история успешности на похожих задачах
    (0..1, выше — лучше); при равной connectivity/квоте переупорядочивает
    цепочку приоритета, не заменяет её целиком.
    """
    chain = list(priority_override or DEFAULT_PRIORITY[task_type])
    connected = set(registry.connected())

    candidates = [name for name in chain if name in connected]
    if not candidates:
        raise NoProviderAvailableError(
            f"Нет подключенных провайдеров для задачи типа {task_type.value}. "
            f"Подключи один из: {', '.join(p.value for p in chain)} в Настройках."
        )

    if success_scores:
        candidates.sort(key=lambda name: success_scores.get(name, 0.0), reverse=True)

    for name in candidates:
        estimate = registry.get(name).estimate_quota()
        if not _is_quota_exhausted(estimate):
            return name

    # Все кандидаты формально на пределе квоты — всё равно берём первого
    # по приоритету, пусть попытка упадёт с ProviderQuotaExceededError и
    # уйдёт в HANDOVER, чем блокировать задачу совсем.
    return candidates[0]


def fallback_chain(task_type: TaskType) -> list[ProviderName]:
    return list(DEFAULT_PRIORITY[task_type])
