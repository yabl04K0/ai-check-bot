from __future__ import annotations

from app.db.models import Job, TaskType
from app.tasks.branching import topic_branch_name


def _job(task_type: TaskType, job_id: int = 42) -> Job:
    job = Job(task_type=task_type, progress_total=1)
    job.id = job_id
    return job


def test_check_full_uses_chek_prefix():
    name = topic_branch_name(_job(TaskType.CHECK_FULL))
    assert name.startswith("chek/bot-job42-")


def test_fix_uses_fix_prefix():
    name = topic_branch_name(_job(TaskType.FIX))
    assert name.startswith("fix/bot-job42-")


def test_feature_uses_feat_prefix():
    name = topic_branch_name(_job(TaskType.FEATURE))
    assert name.startswith("feat/bot-job42-")


def test_refactor_uses_chore_prefix():
    name = topic_branch_name(_job(TaskType.REFACTOR))
    assert name.startswith("chore/bot-job42-")


def test_branch_name_is_git_safe():
    name = topic_branch_name(_job(TaskType.CUSTOM, job_id=7))
    assert " " not in name
    assert name.count("/") == 1
