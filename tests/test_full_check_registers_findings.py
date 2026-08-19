"""Full ЧЕК должен реально дописывать находки в chek_open.md проекта, не
только показывать текст отчёта человеку — см. Step8bRegisterFindings в
app/tasks/protocol_full.py."""

from __future__ import annotations

from app.db.models import Project, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AIProvider, AuthStatus, ProviderResult, RunOptions
from app.registry_store.store import Registry, RegistryFinding, read_registry, write_registry
from app.tasks.factory import build_pipeline
from app.tasks.pipeline import StepContext
from app.tasks.queue import JobQueue

FINDINGS_RESPONSE = (
    "critical|Demo|app/auth.py::validate_token|Токен не проверяется на None\n"
    "high|Demo|app/db.py::save|Не закрывается соединение\n"
)


class ScriptedProvider(AIProvider):
    """Возвращает структурированные находки на шаге gap-finder/register,
    заглушку — на остальных шагах (нам важен только реестр в этом тесте)."""

    name = ProviderName.CLAUDE

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        if "severity|project|file::symbol" in prompt:
            return ProviderResult(text=FINDINGS_RESPONSE, model="fake")
        return ProviderResult(text="[stub]", model="fake")


def test_full_check_writes_findings_to_registry(db, tmp_path):
    with get_session() as session:
        project = Project(name="Demo", repo_full_name="owner/demo", local_path=str(tmp_path))
        session.add(project)
        session.flush()

        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [project.id], comment="прогон")
        ctx = StepContext(job=job, projects=[project], provider=ScriptedProvider(), session=session)
        pipeline = build_pipeline(TaskType.CHECK_FULL)
        pipeline.run(ctx, queue)

    registry = read_registry(tmp_path)
    assert len(registry.open) == 2
    by_symbol = {f.file_symbol: f for f in registry.open}
    assert by_symbol["app/auth.py::validate_token"].severity == "critical"
    assert by_symbol["app/db.py::save"].severity == "high"
    assert by_symbol["app/auth.py::validate_token"].attempts == 0


def test_second_full_check_run_bumps_instead_of_duplicating(db, tmp_path):
    def run_once(session):
        project = session.query(Project).filter_by(name="Demo").first()
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [project.id], comment="прогон")
        ctx = StepContext(job=job, projects=[project], provider=ScriptedProvider(), session=session)
        build_pipeline(TaskType.CHECK_FULL).run(ctx, queue)

    with get_session() as session:
        project = Project(name="Demo", repo_full_name="owner/demo", local_path=str(tmp_path))
        session.add(project)
        session.flush()
        run_once(session)

    with get_session() as session:
        run_once(session)

    registry = read_registry(tmp_path)
    assert len(registry.open) == 2  # не задвоилось
    assert registry.open[0].attempts == 1  # второй прогон = +1 attempt


def test_default_scope_respects_deferred_findings(db, tmp_path):
    """Одна из двух находок уже помечена Never — по умолчанию (без скоупа
    "ЧЕК всё") Full ЧЕК её не должен переоткрывать."""
    write_registry(
        tmp_path,
        Registry(
            never=[
                RegistryFinding(
                    file_symbol="app/auth.py::validate_token", description="решили что ок", reason="не баг"
                )
            ]
        ),
    )
    with get_session() as session:
        project = Project(name="Demo", repo_full_name="owner/demo", local_path=str(tmp_path))
        session.add(project)
        session.flush()

        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [project.id], scope="all", comment="прогон")
        ctx = StepContext(
            job=job, projects=[project], provider=ScriptedProvider(), session=session, scope="all"
        )
        build_pipeline(TaskType.CHECK_FULL).run(ctx, queue)

    registry = read_registry(tmp_path)
    open_symbols = {f.file_symbol for f in registry.open}
    assert "app/auth.py::validate_token" not in open_symbols  # уважили Never
    assert "app/db.py::save" in open_symbols  # вторая находка — обычная, регистрируется
    assert len(registry.never) == 1  # Never не тронут


def test_ignore_registry_scope_reopens_deferred_findings(db, tmp_path):
    """Скоуп "ЧЕК всё" — намеренно переоткрывает даже Never/Later."""
    write_registry(
        tmp_path,
        Registry(
            never=[
                RegistryFinding(
                    file_symbol="app/auth.py::validate_token", description="решили что ок", reason="не баг"
                )
            ]
        ),
    )
    with get_session() as session:
        project = Project(name="Demo", repo_full_name="owner/demo", local_path=str(tmp_path))
        session.add(project)
        session.flush()

        queue = JobQueue(session)
        job = queue.enqueue(
            TaskType.CHECK_FULL, [project.id], scope="all_ignore_registry", comment="прогон"
        )
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=ScriptedProvider(),
            session=session,
            scope="all_ignore_registry",
        )
        build_pipeline(TaskType.CHECK_FULL).run(ctx, queue)

    registry = read_registry(tmp_path)
    open_symbols = {f.file_symbol for f in registry.open}
    assert "app/auth.py::validate_token" in open_symbols  # переоткрыто
    assert registry.never == []  # больше не в Never
