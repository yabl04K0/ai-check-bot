"""Чистая логика решения автопроверки — вынесена из scheduler/autocheck.py,
чтобы её мог использовать и реальный тик, и dry-run в админке без побочных
эффектов (никакого enqueue, никакого обращения к БД)."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import AutocheckSettings
from app.db.models import TaskType
from app.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class AutocheckDecision:
    would_run: bool
    task_type: TaskType | None
    reason: str
    worst_used_pct: float | None = None


def decide_autocheck_action(
    autocheck: AutocheckSettings, *, enabled: bool, registry: ProviderRegistry
) -> AutocheckDecision:
    if not enabled:
        return AutocheckDecision(would_run=False, task_type=None, reason="Автопроверка выключена глобально.")

    connected = registry.connected()
    if not connected:
        return AutocheckDecision(would_run=False, task_type=None, reason="Нет подключенных провайдеров.")

    estimates = [registry.get(name).estimate_quota() for name in connected]
    used_values = [e.used_pct for e in estimates if e.used_pct is not None]
    if not used_values:
        return AutocheckDecision(
            would_run=False, task_type=None, reason="Ни у одного подключенного провайдера нет оценки квоты."
        )
    worst_used = max(used_values)

    if worst_used >= (100 - autocheck.full_threshold_pct):
        full_threshold = 100 - autocheck.full_threshold_pct
        return AutocheckDecision(
            would_run=True,
            task_type=TaskType.CHECK_FULL,
            reason=f"Квота использована {worst_used:.0f}% ≥ порога Full ({full_threshold:.0f}%).",
            worst_used_pct=worst_used,
        )

    hours_values = [e.hours_to_reset for e in estimates if e.hours_to_reset is not None]
    soon_reset = any(h < autocheck.lite_hours_before_reset for h in hours_values)
    under_lite_cap = worst_used < autocheck.lite_threshold_pct
    if soon_reset and under_lite_cap:
        return AutocheckDecision(
            would_run=True,
            task_type=TaskType.CHECK_LITE,
            reason=(
                f"Меньше {autocheck.lite_hours_before_reset}ч до сброса лимита и "
                f"квота использована {worst_used:.0f}% < {autocheck.lite_threshold_pct}%."
            ),
            worst_used_pct=worst_used,
        )

    return AutocheckDecision(
        would_run=False,
        task_type=None,
        reason=f"Квота использована {worst_used:.0f}%, условия для Full/Lite не выполнены.",
        worst_used_pct=worst_used,
    )
