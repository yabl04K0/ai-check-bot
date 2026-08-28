"""HANDOVER — синхронизация LAST_PROMPT.md/STATE_LOG.md/PROJECT_MEMORY.md
на конце прогона job'ы (см. app/tasks/handover.py, вызывается из
app.bot.job_runner._run_pipeline_blocking сразу после того, как пайплайн
дошёл до терминального статуса)."""

from __future__ import annotations

from app.db.models import Job, JobStatus, Project, TaskType
from app.tasks.handover import run_handover


def _job(status: JobStatus, **kwargs) -> Job:
    return Job(task_type=TaskType.CHECK_FULL, status=status, progress_total=1, **kwargs)


def _project(path) -> Project:
    return Project(name="P", repo_full_name="o/p", local_path=str(path))


def test_run_handover_skips_projects_without_local_path():
    job = _job(JobStatus.DONE, report_text="все ок")
    project = Project(name="P", repo_full_name="o/p", local_path=None)

    run_handover(job, [project])  # не должно упасть без local_path


def test_run_handover_writes_state_log_entry(tmp_path):
    job = _job(JobStatus.DONE, report_text="все ок", comment="проверь auth")

    run_handover(job, [_project(tmp_path)])

    text = (tmp_path / "STATE_LOG.md").read_text(encoding="utf-8")
    assert "--- [HANDOVER]" in text
    assert "проверь auth" in text
    assert "job finished normally" in text


def test_run_handover_updates_last_prompt_when_report_present(tmp_path):
    job = _job(JobStatus.DONE, report_text="нашёл 2 проблемы")

    run_handover(job, [_project(tmp_path)])

    text = (tmp_path / "LAST_PROMPT.md").read_text(encoding="utf-8")
    assert "нашёл 2 проблемы" in text


def test_run_handover_skips_last_prompt_when_no_report(tmp_path):
    job = _job(JobStatus.ERROR, report_text=None, handover_note="упало на шаге 5")

    run_handover(job, [_project(tmp_path)])

    assert not (tmp_path / "LAST_PROMPT.md").exists()
    text = (tmp_path / "STATE_LOG.md").read_text(encoding="utf-8")
    assert "упало на шаге 5" in text
    assert "job stopped on error" in text


def test_run_handover_appends_project_memory_only_if_file_exists(tmp_path):
    job = _job(JobStatus.DONE, report_text="сделано")
    run_handover(job, [_project(tmp_path)])
    assert not (tmp_path / "PROJECT_MEMORY.md").exists()  # бот не создаёт его сам

    (tmp_path / "PROJECT_MEMORY.md").write_text("# SESSION LOG\n\n--- old ---\nold\n", encoding="utf-8")
    run_handover(job, [_project(tmp_path)])
    text = (tmp_path / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    assert "сделано" in text
    assert "old" in text


def test_run_handover_paused_quota_status_note(tmp_path):
    job = _job(JobStatus.PAUSED_QUOTA, report_text=None)

    run_handover(job, [_project(tmp_path)])

    text = (tmp_path / "STATE_LOG.md").read_text(encoding="utf-8")
    assert "quota exhausted" in text
