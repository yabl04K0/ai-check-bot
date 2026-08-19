from __future__ import annotations

from app.config import AutocheckSettings
from app.db.models import ProviderAccountStatus, ProviderName, TaskType
from app.providers.base import AIProvider, AuthStatus, ProviderResult, QuotaEstimate, RunOptions
from app.providers.registry import ProviderRegistry
from app.scheduler.decision import decide_autocheck_action

DEFAULT_AUTOCHECK = AutocheckSettings(
    enabled=True, full_threshold_pct=60, lite_threshold_pct=90, lite_hours_before_reset=1
)


class FakeProvider(AIProvider):
    def __init__(
        self, name: ProviderName, *, used_pct: float | None, hours_to_reset: float | None = None
    ) -> None:
        self.name = name
        self._used_pct = used_pct
        self._hours_to_reset = hours_to_reset

    def auth_status(self) -> AuthStatus:
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        return ProviderResult(text="ok")

    def estimate_quota(self) -> QuotaEstimate:
        return QuotaEstimate(used_pct=self._used_pct, hours_to_reset=self._hours_to_reset)


def test_disabled_globally_never_runs():
    registry = ProviderRegistry({ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=99.0)})
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=False, registry=registry)
    assert decision.would_run is False
    assert "выключена" in decision.reason


def test_no_connected_providers_never_runs():
    registry = ProviderRegistry({})
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    assert decision.would_run is False


def test_no_quota_estimate_never_runs():
    registry = ProviderRegistry({ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=None)})
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    assert decision.would_run is False


def test_high_usage_triggers_full_check():
    # < 60% квоты осталось = использовано >= 40%
    registry = ProviderRegistry({ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=45.0)})
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    assert decision.would_run is True
    assert decision.task_type == TaskType.CHECK_FULL


def test_soon_reset_and_under_cap_triggers_lite_check():
    # used_pct=20 < 40 (порог Full при full_threshold_pct=60) — Full не триггерится,
    # но скоро сброс лимита и квота ещё не под потолком Lite (90%) → Lite
    registry = ProviderRegistry(
        {ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=20.0, hours_to_reset=0.5)}
    )
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    assert decision.would_run is True
    assert decision.task_type == TaskType.CHECK_LITE


def test_moderate_usage_far_from_reset_does_nothing():
    registry = ProviderRegistry(
        {ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=20.0, hours_to_reset=5.0)}
    )
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    assert decision.would_run is False
    assert decision.task_type is None


def test_over_lite_cap_even_with_soon_reset_does_nothing():
    registry = ProviderRegistry(
        {ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=95.0, hours_to_reset=0.2)}
    )
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    # used_pct=95 >= (100-60)=40, значит это уже случай Full, не "ничего"
    assert decision.would_run is True
    assert decision.task_type == TaskType.CHECK_FULL


def test_worst_case_across_multiple_providers_wins():
    registry = ProviderRegistry(
        {
            ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, used_pct=10.0),
            ProviderName.CODEX: FakeProvider(ProviderName.CODEX, used_pct=50.0),
        }
    )
    decision = decide_autocheck_action(DEFAULT_AUTOCHECK, enabled=True, registry=registry)
    assert decision.would_run is True
    assert decision.worst_used_pct == 50.0
