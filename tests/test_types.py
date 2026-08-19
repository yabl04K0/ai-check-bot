from __future__ import annotations

import pytest

from app.db.models import TaskType
from app.tasks.types import TaskSpec


def test_check_full_does_not_require_comment():
    spec = TaskSpec(task_type=TaskType.CHECK_FULL, project_ids=(1,))
    spec.validate()  # не должно кидать


def test_feature_requires_description():
    spec = TaskSpec(task_type=TaskType.FEATURE, project_ids=(1,), comment=None)
    with pytest.raises(ValueError):
        spec.validate()


def test_feature_with_comment_is_valid():
    spec = TaskSpec(task_type=TaskType.FEATURE, project_ids=(1,), comment="добавить экспорт в CSV")
    spec.validate()


def test_no_projects_raises():
    spec = TaskSpec(task_type=TaskType.CHECK_LITE, project_ids=())
    with pytest.raises(ValueError):
        spec.validate()
