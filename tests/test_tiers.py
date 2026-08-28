"""Приоритеты аккаунтов (app.providers.tiers) — глобальный тумблер,
CRUD тиров, round-robin выдача (TierPicker) и роутинг вызова в другой
провайдер через run_prompt_with_tier (см. запрос пользователя: "хочу
задавать некоторые акки как акки для делегации работы")."""

from __future__ import annotations

from app.db.models import AccountPriority, Job, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AuthStatus, ProviderQuotaExceededError, ProviderResult, RunOptions
from app.providers.tiers import (
    TierAccount,
    TierPicker,
    accounts_in_tier,
    all_known_accounts,
    call_tier_account,
    delegation_mode_enabled,
    get_tier,
    job_has_tier_overrides,
    job_tier_assignments,
    run_prompt_with_tier,
    seed_default_tier,
    set_delegation_mode,
    set_job_tier,
    set_tier,
)
from app.tasks.pipeline import StepContext


def test_delegation_mode_disabled_by_default(db):
    assert delegation_mode_enabled() is False


def test_set_delegation_mode_persists(db):
    set_delegation_mode(True)
    assert delegation_mode_enabled() is True
    set_delegation_mode(False)
    assert delegation_mode_enabled() is False


def test_get_tier_none_when_unset(db):
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") is None


def test_set_then_get_tier(db):
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)
    assert get_tier(ProviderName.CLAUDE_CODE, "primary") == AccountPriority.HEAD


def test_set_tier_updates_existing(db):
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)
    assert get_tier(ProviderName.GROQ, "extra:1") == AccountPriority.DELEGATION


def test_set_tier_none_clears_assignment(db):
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "extra:1", None)
    assert get_tier(ProviderName.GROQ, "extra:1") is None


def test_accounts_in_tier_lists_only_matching_priority(db):
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)
    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)

    delegation = accounts_in_tier(AccountPriority.DELEGATION)
    assert set(delegation) == {
        TierAccount(ProviderName.GROQ, "primary"),
        TierAccount(ProviderName.GROQ, "extra:1"),
    }


def test_seed_default_tier_covers_primary_and_extra(db, monkeypatch):
    import app.providers.tiers as tiers_mod
    from app.providers.accounts_store import AccountEntry

    monkeypatch.setattr(
        tiers_mod,
        "list_extra_accounts",
        lambda provider: [AccountEntry(id=1, secret="x"), AccountEntry(id=2, secret="y")],
    )

    seed_default_tier(ProviderName.CLAUDE_CODE, AccountPriority.HEAD)

    assert get_tier(ProviderName.CLAUDE_CODE, "primary") == AccountPriority.HEAD
    assert get_tier(ProviderName.CLAUDE_CODE, "extra:1") == AccountPriority.HEAD
    assert get_tier(ProviderName.CLAUDE_CODE, "extra:2") == AccountPriority.HEAD


def test_seed_default_tier_does_not_overwrite_existing_choice(db, monkeypatch):
    import app.providers.tiers as tiers_mod

    monkeypatch.setattr(tiers_mod, "list_extra_accounts", lambda provider: [])

    set_tier(ProviderName.CLAUDE_CODE, "primary", AccountPriority.DELEGATION)
    seed_default_tier(ProviderName.CLAUDE_CODE, AccountPriority.HEAD)

    assert get_tier(ProviderName.CLAUDE_CODE, "primary") == AccountPriority.DELEGATION


def test_tier_picker_round_robins_across_accounts(db):
    set_tier(ProviderName.GROQ, "primary", AccountPriority.DELEGATION)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)

    picker = TierPicker()
    picks = [picker.pick(AccountPriority.DELEGATION) for _ in range(4)]
    labels = [p.account_label for p in picks]
    assert labels == ["primary", "extra:1", "primary", "extra:1"]


def test_tier_picker_returns_none_when_tier_empty(db):
    picker = TierPicker()
    assert picker.pick(AccountPriority.HEAD) is None


def test_tier_picker_pick_all_returns_empty_list_when_tier_empty(db):
    picker = TierPicker()
    assert picker.pick_all(AccountPriority.HEAD) == []


def test_tier_picker_pick_all_returns_full_rotated_list(db):
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.MISTRAL, "primary", AccountPriority.HEAD)

    picker = TierPicker()
    first = [a.provider for a in picker.pick_all(AccountPriority.HEAD)]
    second = [a.provider for a in picker.pick_all(AccountPriority.HEAD)]

    assert set(first) == {ProviderName.GROQ, ProviderName.MISTRAL}
    assert len(first) == 2
    assert first != second
    assert set(first) == set(second)


def test_all_known_accounts_lists_primary_and_extra(db, monkeypatch):
    import app.providers.tiers as tiers_mod
    from app.providers.accounts_store import AccountEntry

    monkeypatch.setattr(tiers_mod, "list_extra_accounts", lambda provider: [AccountEntry(id=1, secret="x")])

    class FakeProvider:
        def auth_status(self):
            return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    class FakeRegistry:
        def all(self):
            return {ProviderName.CLAUDE_CODE: FakeProvider()}

    accounts = all_known_accounts(FakeRegistry())
    assert accounts == [
        TierAccount(ProviderName.CLAUDE_CODE, "primary"),
        TierAccount(ProviderName.CLAUDE_CODE, "extra:1"),
    ]


def _make_job(db) -> int:
    with get_session() as session:
        job = Job(task_type=TaskType.CHECK_FULL)
        session.add(job)
        session.flush()
        return job.id


class _FakeProvider:
    def __init__(self, name, text, error=None):
        self.name = name
        self._text = text
        self._error = error
        self.calls = []

    def run_prompt(self, prompt, options=None):
        self.calls.append(options)
        if self._error is not None:
            raise self._error
        return ProviderResult(text=self._text)


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


def test_run_prompt_with_tier_uses_ctx_provider_when_delegation_disabled(db):
    job_id = _make_job(db)
    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry({ProviderName.CLAUDE_CODE: ctx_provider}),
    )
    options = RunOptions()
    result = run_prompt_with_tier(ctx, AccountPriority.HEAD, "hi", options)
    assert result.text == "from ctx.provider"
    assert ctx_provider.calls == [options]


def test_run_prompt_with_tier_falls_back_when_tier_empty(db):
    set_delegation_mode(True)
    job_id = _make_job(db)
    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry({ProviderName.CLAUDE_CODE: ctx_provider}),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.DELEGATION, "hi", RunOptions())
    assert result.text == "from ctx.provider"


def test_run_prompt_with_tier_routes_to_different_provider(db):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)
    job_id = _make_job(db)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    groq_provider = _FakeProvider(ProviderName.GROQ, "from groq")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {ProviderName.CLAUDE_CODE: ctx_provider, ProviderName.GROQ: groq_provider}
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.DELEGATION, "hi", RunOptions())
    assert result.text == "from groq"
    assert groq_provider.calls[0].forced_account_label == "extra:1"
    assert ctx_provider.calls == []


def test_job_has_tier_overrides_false_until_set(db):
    job_id = _make_job(db)
    assert job_has_tier_overrides(job_id) is False
    set_job_tier(job_id, ProviderName.GROQ, "primary", AccountPriority.HEAD)
    assert job_has_tier_overrides(job_id) is True


def test_job_tier_assignments_scoped_to_one_job(db):
    job_a = _make_job(db)
    job_b = _make_job(db)
    set_job_tier(job_a, ProviderName.GROQ, "primary", AccountPriority.HEAD)
    set_job_tier(job_b, ProviderName.CLAUDE_CODE, "primary", AccountPriority.DELEGATION)

    assert job_tier_assignments(job_a) == {TierAccount(ProviderName.GROQ, "primary"): AccountPriority.HEAD}
    assert job_tier_assignments(job_b) == {
        TierAccount(ProviderName.CLAUDE_CODE, "primary"): AccountPriority.DELEGATION
    }


def test_tier_picker_with_job_id_uses_only_job_overrides(db):
    # Глобальный тир на GROQ:primary — НЕ должен попасть в picker, раз у
    # job_id задан свой оверрайд без этого аккаунта (см. запрос
    # пользователя: "если не поставил приоритет на какую то ии то значит
    # не используем её в задаче").
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)
    job_id = _make_job(db)
    set_job_tier(job_id, ProviderName.CLAUDE_CODE, "primary", AccountPriority.HEAD)

    picker = TierPicker(job_id)
    assert picker.pick(AccountPriority.HEAD) == TierAccount(ProviderName.CLAUDE_CODE, "primary")
    # Второй pick того же (единственного) тира — тот же аккаунт по кругу,
    # GROQ:primary в выдаче не появляется вообще.
    assert picker.pick(AccountPriority.HEAD) == TierAccount(ProviderName.CLAUDE_CODE, "primary")


def test_run_prompt_with_tier_uses_job_override_even_when_delegation_disabled(db):
    """Оверрайд задачи работает НЕЗАВИСИМО от глобального тумблера
    Настроек — выбор ИИ для конкретной задачи сам по себе намерение."""
    assert delegation_mode_enabled() is False
    job_id = _make_job(db)
    set_job_tier(job_id, ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    groq_provider = _FakeProvider(ProviderName.GROQ, "from groq")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {ProviderName.CLAUDE_CODE: ctx_provider, ProviderName.GROQ: groq_provider}
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.DELEGATION, "hi", RunOptions())
    assert result.text == "from groq"
    assert groq_provider.calls[0].forced_account_label == "extra:1"


def test_run_prompt_with_tier_job_override_excludes_globally_assigned_account(db):
    """GROQ:primary имеет глобальный тир HEAD, но у job_id нет оверрайда
    ДЛЯ НЕГО (только для CLAUDE_CODE) — GROQ:primary в этой задаче не
    участвует, вызов идёт в ctx.provider (не в groq)."""
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)
    job_id = _make_job(db)
    set_job_tier(job_id, ProviderName.CLAUDE_CODE, "primary", AccountPriority.DELEGATION)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    groq_provider = _FakeProvider(ProviderName.GROQ, "from groq")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {ProviderName.CLAUDE_CODE: ctx_provider, ProviderName.GROQ: groq_provider}
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.HEAD, "hi", RunOptions())
    assert result.text == "from ctx.provider"  # HEAD-тир job-оверрайда пуст
    assert groq_provider.calls == []


def test_run_prompt_with_tier_falls_back_when_assigned_provider_disabled(db):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)
    job_id = _make_job(db)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    groq_provider = _FakeProvider(ProviderName.GROQ, "from groq")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {ProviderName.CLAUDE_CODE: ctx_provider, ProviderName.GROQ: groq_provider},
            disabled={ProviderName.GROQ},
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.DELEGATION, "hi", RunOptions())
    assert result.text == "from ctx.provider"


class _FailingProvider:
    """A provider that raises an error on run_prompt (for testing fallback)."""

    def __init__(self, name, error):
        self.name = name
        self._error = error
        self.calls = []

    def run_prompt(self, prompt, options=None):
        self.calls.append(options)
        raise self._error


def test_run_prompt_with_tier_falls_back_to_ctx_provider_when_assigned_account_fails(db):
    """When the tier-assigned provider fails, fallback to ctx.provider."""
    from app.providers.base import ProviderQuotaExceededError

    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.DELEGATION)
    job_id = _make_job(db)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    failing_groq = _FailingProvider(ProviderName.GROQ, ProviderQuotaExceededError("quota gone"))
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {ProviderName.CLAUDE_CODE: ctx_provider, ProviderName.GROQ: failing_groq}
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.DELEGATION, "hi", RunOptions())
    assert result.text == "from ctx.provider"
    assert len(failing_groq.calls) == 1
    assert len(ctx_provider.calls) == 1


def test_run_prompt_with_tier_retries_next_account_before_falling_back(db):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.MISTRAL, "primary", AccountPriority.HEAD)
    job_id = _make_job(db)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    failing_groq = _FakeProvider(ProviderName.GROQ, "unused", error=ProviderQuotaExceededError("no quota"))
    working_mistral = _FakeProvider(ProviderName.MISTRAL, "from mistral")
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {
                ProviderName.CLAUDE_CODE: ctx_provider,
                ProviderName.GROQ: failing_groq,
                ProviderName.MISTRAL: working_mistral,
            }
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.HEAD, "hi", RunOptions())
    assert result.text == "from mistral"
    assert len(failing_groq.calls) == 1
    assert len(working_mistral.calls) == 1
    assert ctx_provider.calls == []


def test_run_prompt_with_tier_falls_back_when_all_tier_accounts_fail(db):
    set_delegation_mode(True)
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.MISTRAL, "primary", AccountPriority.HEAD)
    job_id = _make_job(db)

    ctx_provider = _FakeProvider(ProviderName.CLAUDE_CODE, "from ctx.provider")
    failing_groq = _FakeProvider(ProviderName.GROQ, "unused", error=ProviderQuotaExceededError("no quota"))
    failing_mistral = _FakeProvider(
        ProviderName.MISTRAL, "unused", error=ProviderQuotaExceededError("no quota")
    )
    ctx = StepContext(
        job=type("J", (), {"id": job_id})(),
        projects=[],
        provider=ctx_provider,
        session=None,
        provider_registry=_FakeRegistry(
            {
                ProviderName.CLAUDE_CODE: ctx_provider,
                ProviderName.GROQ: failing_groq,
                ProviderName.MISTRAL: failing_mistral,
            }
        ),
    )
    result = run_prompt_with_tier(ctx, AccountPriority.HEAD, "hi", RunOptions())
    assert result.text == "from ctx.provider"
    assert len(failing_groq.calls) == 1
    assert len(failing_mistral.calls) == 1
    assert len(ctx_provider.calls) == 1


def test_call_tier_account_retries_next_account_when_first_fails(db):
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)
    set_tier(ProviderName.MISTRAL, "primary", AccountPriority.HEAD)

    failing_groq = _FakeProvider(ProviderName.GROQ, "unused", error=ProviderQuotaExceededError("no quota"))
    working_mistral = _FakeProvider(ProviderName.MISTRAL, "from mistral")
    registry = _FakeRegistry({ProviderName.GROQ: failing_groq, ProviderName.MISTRAL: working_mistral})

    outcome = call_tier_account(AccountPriority.HEAD, registry, "hi")

    assert outcome is not None
    account, result = outcome
    assert account.provider == ProviderName.MISTRAL
    assert result.text == "from mistral"
    assert len(failing_groq.calls) == 1


def test_call_tier_account_returns_none_when_all_accounts_fail(db):
    set_tier(ProviderName.GROQ, "primary", AccountPriority.HEAD)

    failing_groq = _FakeProvider(ProviderName.GROQ, "unused", error=ProviderQuotaExceededError("no quota"))
    registry = _FakeRegistry({ProviderName.GROQ: failing_groq})

    outcome = call_tier_account(AccountPriority.HEAD, registry, "hi")

    assert outcome is None
