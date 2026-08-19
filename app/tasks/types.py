"""Типы задач — независимое измерение от провайдера (см. README).

Тип задачи выбирается отдельно от того, кто её выполняет. Не смешивать
эти два измерения в коде: TaskType никогда не должен определять
провайдера напрямую — только через app.providers.router.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Severity, TaskType

# Общее число шагов пайплайна на тип задачи — для прогресс-бара
# (% = завершённые шаги / всего, см. README и ui-map.mermaid).
STEP_COUNT: dict[TaskType, int] = {
    TaskType.CHECK_FULL: 13,
    TaskType.CHECK_LITE: 4,
    TaskType.FEATURE: 4,   # план → написать → протестировать → показать диф
    TaskType.FIX: 4,
    TaskType.REFACTOR: 4,
    TaskType.CUSTOM: 4,
}

TASK_TYPE_LABELS: dict[TaskType, str] = {
    TaskType.CHECK_FULL: "🔴 Full ЧЕК",
    TaskType.CHECK_LITE: "🟢 Lite ЧЕК",
    TaskType.FEATURE: "✨ Фича",
    TaskType.FIX: "🔧 Фикс",
    TaskType.REFACTOR: "♻️ Рефакторинг",
    TaskType.CUSTOM: "📝 Кастом",
}

# Для Фичи/Фикса/Рефакторинга/Кастома текст-описание задачи обязателен
# (аналог "комментария" у Чека, но не опция, а суть задачи).
REQUIRES_DESCRIPTION: set[TaskType] = {
    TaskType.FEATURE,
    TaskType.FIX,
    TaskType.REFACTOR,
    TaskType.CUSTOM,
}

SEVERITY_EMOJI: dict[Severity, str] = {
    Severity.CRITICAL: "🟥",
    Severity.HIGH: "🟧",
    Severity.MEDIUM: "🟨",
}


@dataclass(frozen=True)
class TaskSpec:
    """Валидированный вход на запуск задачи, до попадания в очередь."""

    task_type: TaskType
    project_ids: tuple[int, ...]
    scope: str | None = None
    comment: str | None = None

    def validate(self) -> None:
        if not self.project_ids:
            raise ValueError("Нужно выбрать хотя бы один проект.")
        if self.task_type in REQUIRES_DESCRIPTION and not (self.comment and self.comment.strip()):
            raise ValueError(
                f"Для {TASK_TYPE_LABELS[self.task_type]} описание задачи обязательно."
            )
