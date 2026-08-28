from __future__ import annotations

import app.tasks.protocol_full as protocol_full_mod
from app.db.models import Job, Project, ProviderAccountStatus, ProviderMode, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AIProvider, AuthStatus, ProviderResult, QuotaEstimate
from app.providers.tiers import AccountPriority, set_delegation_mode, set_tier
from app.tasks.pipeline import StepContext
from app.tasks.protocol_full import FLEET_CHECKER_DOMAINS_DEFAULT, Step5FleetPlanner


class DomainsProvider(AIProvider):
    name = ProviderName.CLAUDE

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None) -> ProviderResult:
        return ProviderResult(text="auth\napi\ndb\nfrontend")


class _FakeRegistry:
    def __init__(self, providers, disabled=frozenset()):
        self._providers = providers
        self._disabled = disabled

    def all(self):
        return dict(self._providers)

    def get(self, name):
        return self._providers[name]

    def is_disabled(self, name):
        return name in self._disabled


def _make_job_and_project(session, *, provider_mode: ProviderMode = ProviderMode.AUTO):
    project = Project(name="P", repo_full_name="owner/p")
    session.add(project)
    session.flush()
    job = Job(task_type=TaskType.CHECK_FULL, provider_mode=provider_mode, progress_total=13)
    job.projects = [project]
    session.add(job)
    session.flush()
    return job, project


def test_fleet_budget_not_applicable_without_registry(db):
    with get_session() as session:
        job, project = _make_job_and_project(session)
        ctx = StepContext(job=job, projects=[project], provider=DomainsProvider(), session=session)

        Step5FleetPlanner().run(ctx)

        assert len(ctx.state["domains"]) == FLEET_CHECKER_DOMAINS_DEFAULT
        assert "fleet_budget_note" not in ctx.state


def test_fleet_budget_not_applicable_when_delegation_off(db):
    set_delegation_mode(False)
    with get_session() as session:
        job, project = _make_job_and_project(session)
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=DomainsProvider(),
            session=session,
            provider_registry=_FakeRegistry({ProviderName.CLAUDE: DomainsProvider()}),
        )

        Step5FleetPlanner().run(ctx)

        assert len(ctx.state["domains"]) == FLEET_CHECKER_DOMAINS_DEFAULT
        assert "fleet_budget_note" not in ctx.state


def test_fleet_budget_reduces_domains_when_accounts_scarce(db):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)

    with get_session() as session:
        job, project = _make_job_and_project(session)
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=DomainsProvider(),
            session=session,
            provider_registry=_FakeRegistry(
                {ProviderName.CLAUDE: DomainsProvider(), ProviderName.GROQ: DomainsProvider()},
                disabled=frozenset(),
            ),
        )

        Step5FleetPlanner().run(ctx)

        assert len(ctx.state["domains"]) == 2
        assert "fleet_budget_note" in ctx.state
        assert "2" in ctx.state["fleet_budget_note"]


def test_fleet_budget_filters_disabled_circuit_open_and_exhausted(db, monkeypatch):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    set_tier(ProviderName.MISTRAL, "primary", AccountPriority.DELEGATION)
    set_tier(ProviderName.DEEPSEEK, "primary", AccountPriority.DELEGATION)

    protocol_full_mod.circuit_breaker.record_failure(ProviderName.MISTRAL, "primary")

    def fake_quota_estimate(registry, provider, account_label):
        if provider == ProviderName.DEEPSEEK:
            return QuotaEstimate(used_pct=95.0, hours_to_reset=None, is_estimate=False)
        return QuotaEstimate(used_pct=None, hours_to_reset=None)

    monkeypatch.setattr(protocol_full_mod, "account_quota_estimate_for", fake_quota_estimate)

    with get_session() as session:
        job, project = _make_job_and_project(session)
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=DomainsProvider(),
            session=session,
            provider_registry=_FakeRegistry(
                {
                    ProviderName.CLAUDE: DomainsProvider(),
                    ProviderName.GROQ: DomainsProvider(),
                    ProviderName.MISTRAL: DomainsProvider(),
                    ProviderName.DEEPSEEK: DomainsProvider(),
                },
                disabled=frozenset({ProviderName.GROQ}),
            ),
        )

        Step5FleetPlanner().run(ctx)

        assert len(ctx.state["domains"]) == 1
        assert "0" in ctx.state["fleet_budget_note"]


def test_fleet_budget_no_reduction_when_enough_healthy_accounts(db):
    set_delegation_mode(True)
    for label in ("primary", "extra:1", "extra:2", "extra:3"):
        set_tier(ProviderName.GROQ, label, AccountPriority.DELEGATION)

    with get_session() as session:
        job, project = _make_job_and_project(session)
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=DomainsProvider(),
            session=session,
            provider_registry=_FakeRegistry(
                {ProviderName.CLAUDE: DomainsProvider(), ProviderName.GROQ: DomainsProvider()}
            ),
        )

        Step5FleetPlanner().run(ctx)

        assert len(ctx.state["domains"]) == FLEET_CHECKER_DOMAINS_DEFAULT
        assert "fleet_budget_note" not in ctx.state


def test_fleet_budget_note_surfaces_in_final_report(db):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)

    with get_session() as session:
        job, project = _make_job_and_project(session, provider_mode=ProviderMode.AUTO)
        ctx = StepContext(
            job=job,
            projects=[project],
            provider=DomainsProvider(),
            session=session,
            provider_registry=_FakeRegistry(
                {ProviderName.CLAUDE: DomainsProvider(), ProviderName.GROQ: DomainsProvider()}
            ),
        )

        Step5FleetPlanner().run(ctx)
        assert "fleet_budget_note" in ctx.state

        protocol_full_mod.Step13HumanConfirm().run(ctx)
        assert "⚠️" in ctx.state["final_report"]
