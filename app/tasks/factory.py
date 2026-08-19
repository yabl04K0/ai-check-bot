"""Сборка Pipeline под TaskType — единственное место, где типы задач
маппятся на конкретные шаги."""

from __future__ import annotations

from app.db.models import TaskType
from app.tasks import protocol_full, protocol_lite
from app.tasks.generic import build_steps as build_generic_steps
from app.tasks.pipeline import Pipeline

_GENERIC_TYPES = {TaskType.FEATURE, TaskType.FIX, TaskType.REFACTOR, TaskType.CUSTOM}


def build_pipeline(task_type: TaskType) -> Pipeline:
    if task_type == TaskType.CHECK_FULL:
        return Pipeline(protocol_full.build_steps())
    if task_type == TaskType.CHECK_LITE:
        return Pipeline(protocol_lite.build_steps())
    if task_type in _GENERIC_TYPES:
        return Pipeline(build_generic_steps())
    raise ValueError(f"Нет пайплайна для типа задачи: {task_type}")
