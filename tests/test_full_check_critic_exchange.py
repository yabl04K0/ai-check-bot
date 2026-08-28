from __future__ import annotations

import threading

from app.db.models import Job, Project, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AIProvider, AuthStatus, ProviderResult
from app.tasks.pipeline import StepContext
from app.tasks.protocol_full import Step11ConvergenceLoop


class ScriptedProvider(AIProvider):
    name = ProviderName.CLAUDE

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None) -> ProviderResult:
        system = options.system if options else ""
        with self._lock:
            self.calls.append((system, prompt))
        if "fixer" in system:
            return ProviderResult(text="v2")
        if "корректность" in system:
            return ProviderResult(text="Одобрено")
        if "безопасность" in system:
            return ProviderResult(text="LGTM")
        return ProviderResult(text="")


def _make_job_and_project(session):
    project = Project(name="P", repo_full_name="owner/p")
    session.add(project)
    session.flush()
    job = Job(task_type=TaskType.CHECK_FULL, progress_total=13)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job, project


def test_convergence_round_shares_other_critics_prior_opinion(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = ScriptedProvider()
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session)
        ctx.state["fix_proposal"] = "v1"
        ctx.state["critic_a"] = "ищи баг в X"
        ctx.state["critic_b"] = "ищи баг в Y"

        Step11ConvergenceLoop().run(ctx)

        assert ctx.state["convergence_rounds"] == 1
        assert ctx.state["escalated"] is False

        critic_a_calls = [p for s, p in provider.calls if "корректность" in s]
        critic_b_calls = [p for s, p in provider.calls if "безопасность" in s]
        assert len(critic_a_calls) == 1
        assert len(critic_b_calls) == 1
        assert "ищи баг в Y" in critic_a_calls[0]
        assert "ищи баг в X" in critic_b_calls[0]


class NeverConvergingProvider(AIProvider):
    name = ProviderName.CLAUDE

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None) -> ProviderResult:
        system = options.system if options else ""
        self.calls.append((system, prompt))
        if "суммируешь" in system:
            return ProviderResult(text="Спор о app/auth.py::validate_token — нужен ли null-чек")
        if "fixer" in system:
            return ProviderResult(text="v2")
        if "корректность" in system:
            return ProviderResult(text="Есть замечание по null-чеку")
        if "безопасность" in system:
            return ProviderResult(text="Есть замечание по логированию токена")
        return ProviderResult(text="")


def test_escalation_summarizes_disagreement(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = NeverConvergingProvider()
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session)
        ctx.state["fix_proposal"] = "v1"
        ctx.state["critic_a"] = "ищи баг в X"
        ctx.state["critic_b"] = "ищи баг в Y"

        Step11ConvergenceLoop().run(ctx)

        assert ctx.state["escalated"] is True
        assert "validate_token" in ctx.state["escalation_crux"]

        summarizer_calls = [p for s, p in provider.calls if "суммируешь" in s]
        assert len(summarizer_calls) == 1
        assert "Есть замечание по null-чеку" in summarizer_calls[0]
        assert "Есть замечание по логированию токена" in summarizer_calls[0]


def test_no_escalation_means_no_summarizer_call(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = ScriptedProvider()
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session)
        ctx.state["fix_proposal"] = "v1"
        ctx.state["critic_a"] = "ищи баг в X"
        ctx.state["critic_b"] = "ищи баг в Y"

        Step11ConvergenceLoop().run(ctx)

        assert "escalation_crux" not in ctx.state
        summarizer_calls = [p for s, p in provider.calls if "суммируешь" in s]
        assert summarizer_calls == []


def test_first_convergence_round_has_no_other_opinion_yet_when_state_empty(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        provider = ScriptedProvider()
        ctx = StepContext(job=job, projects=[project], provider=provider, session=session)
        ctx.state["fix_proposal"] = "v1"

        Step11ConvergenceLoop().run(ctx)

        critic_a_calls = [p for s, p in provider.calls if "корректность" in s]
        assert "Мнение другого критика" not in critic_a_calls[0]
