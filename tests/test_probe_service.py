import pytest
from sqlalchemy.exc import IntegrityError

from ai_check_bot.config import MAX_PROBES_PER_DAY
from ai_check_bot.models import AIAccount, ProbeRun
from ai_check_bot.probe_service import (
    InvalidTimeError,
    ScheduleLimitError,
    add_account,
    add_schedule,
    run_probe,
)
from ai_check_bot.providers.base import AIProvider, ProbeResult
from ai_check_bot.providers.registry import PROVIDER_REGISTRY


class FakeProvider(AIProvider):
    async def probe(self, message: str) -> ProbeResult:
        if message == "fail-me":
            return ProbeResult(success=False, error="boom")
        return ProbeResult(success=True, latency_ms=42)


@pytest.fixture(autouse=True)
def register_fake_provider():
    PROVIDER_REGISTRY["fake"] = FakeProvider
    yield
    del PROVIDER_REGISTRY["fake"]


def test_add_account(session_factory):
    with session_factory() as session:
        acc = add_account(session, provider="fake", label="a1", api_key="x")
        assert acc.id is not None
        assert acc.provider == "fake"


def test_add_account_duplicate_label_raises(session_factory):
    with session_factory() as session:
        add_account(session, provider="fake", label="dup", api_key="x")
        with pytest.raises(IntegrityError):
            add_account(session, provider="fake", label="dup", api_key="y")


def test_add_schedule_rejects_bad_time(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        with pytest.raises(InvalidTimeError):
            add_schedule(session, account=acc, time_of_day="25:99")


def test_add_schedule_enforces_daily_limit(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        for hour in range(MAX_PROBES_PER_DAY):
            add_schedule(session, account=acc, time_of_day=f"{hour:02d}:00")
        with pytest.raises(ScheduleLimitError):
            add_schedule(session, account=acc, time_of_day="23:59")


async def test_run_probe_success_writes_run(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        run = await run_probe(session, acc, "ping")
        assert run.success is True
        assert run.latency_ms == 42
        assert session.query(ProbeRun).count() == 1


async def test_run_probe_failure_recorded_not_raised(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        run = await run_probe(session, acc, "fail-me")
        assert run.success is False
        assert run.error == "boom"
