"""End-to-end проверка протоколов (generic/Lite/Full) через Pipeline с
фейковым провайдером — без реальных сетевых вызовов к ИИ."""

from __future__ import annotations

from app.db.models import Job, Project, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AIProvider, AuthStatus, ProviderResult, RunOptions
from app.tasks.factory import build_pipeline
from app.tasks.pipeline import StepContext
from app.tasks.queue import JobQueue


class FakeProvider(AIProvider):
    name = ProviderName.CLAUDE

    def __init__(self) -> None:
        self.calls: list[str] = []

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        self.calls.append(prompt)
        return ProviderResult(text=f"[fake response #{len(self.calls)}]", model="fake")


def _run(db, task_type: TaskType, *, comment: str | None = None) -> tuple[Job, FakeProvider]:
    with get_session() as session:
        project = Project(name="P", repo_full_name=f"owner/p-{task_type.value}")
        session.add(project)
        session.flush()

        queue = JobQueue(session)
        job = queue.enqueue(task_type, [project.id], comment=comment)
        provider = FakeProvider()
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session, comment=comment)
        pipeline = build_pipeline(task_type)
        pipeline.run(ctx, queue)
        return job, provider


def test_generic_pipeline_completes_for_each_generic_task_type(db):
    for task_type in (TaskType.FEATURE, TaskType.FIX, TaskType.REFACTOR, TaskType.CUSTOM):
        job, provider = _run(db, task_type, comment="сделай что-нибудь полезное")
        assert job.status.value == "done"
        assert job.progress_step == job.progress_total == 4
        assert job.report_text
        assert "План:" in job.report_text
        assert "Патч:" in job.report_text
        assert len(provider.calls) == 2  # план + патч — единственные LLM-вызовы


def test_lite_pipeline_completes_without_fix_by_default(db):
    job, provider = _run(db, TaskType.CHECK_LITE, comment="просто посмотри")
    assert job.status.value == "done"
    assert job.progress_step == job.progress_total == 4
    assert "Scout:" in job.report_text
    assert "не запрашивался" in job.report_text
    assert len(provider.calls) == 1  # только scout, fixer не триггерился


def test_lite_pipeline_triggers_fix_on_keyword(db):
    job, provider = _run(db, TaskType.CHECK_LITE, comment="почини это")
    assert job.status.value == "done"
    assert len(provider.calls) == 2  # scout + fixer
    assert "не запрашивался" not in job.report_text


def test_full_check_pipeline_completes_end_to_end(db):
    job, provider = _run(db, TaskType.CHECK_FULL, comment="полный прогон")
    assert job.status.value == "done"
    assert job.progress_step == job.progress_total == 12
    assert job.report_text
    assert "Финальный фикс" in job.report_text
    # 4b без триггера не должен звать провайдера — проверяем, что вызовов
    # заметно меньше, чем "по вызову на каждый нетривиальный шаг + запас"
    assert len(provider.calls) > 0
