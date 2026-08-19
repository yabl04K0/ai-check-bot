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

# Приоритет провайдеров по умолчанию для каждого типа задачи (первый — предпочтительный).
# Чек (Full) — штатный флот-протокол заточен под Claude; Lite — легче,
# локалка как scout вытягивает первой, чтобы экономить платную квоту.
DEFAULT_PRIORITY: dict[TaskType, list[ProviderName]] = {
    TaskType.CHECK_FULL: [ProviderName.CLAUDE, ProviderName.CODEX, ProviderName.CURSOR],
    TaskType.CHECK_LITE: [ProviderName.LOCAL_LLM, ProviderName.CLAUDE, ProviderName.CODEX],
    TaskType.FEATURE: [ProviderName.CLAUDE, ProviderName.CURSOR, ProviderName.CODEX],
    TaskType.FIX: [ProviderName.CURSOR, ProviderName.CLAUDE, ProviderName.CODEX],
    TaskType.REFACTOR: [ProviderName.CLAUDE, ProviderName.CURSOR, ProviderName.CODEX],
    TaskType.CUSTOM: [ProviderName.CLAUDE, ProviderName.CODEX, ProviderName.CURSOR, ProviderName.LOCAL_LLM],
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
