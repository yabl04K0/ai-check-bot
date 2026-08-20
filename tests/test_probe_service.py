import pytest
from sqlalchemy.exc import IntegrityError

from ai_check_bot.config import MAX_PROBES_PER_DAY
from ai_check_bot.models import AIAccount, ProbeRun
from ai_check_bot.probe_service import (
    InvalidProxyError,
    InvalidTimeError,
    ScheduleLimitError,
    UnknownProviderError,
    add_account,
    add_schedule,
    delete_account,
    get_account_by_label,
    run_probe,
    set_account_enabled,
    set_account_proxy,
)
from ai_check_bot.providers.base import AIProvider, ProbeResult, TaskResult
from ai_check_bot.providers.registry import PROVIDER_REGISTRY


class FakeProvider(AIProvider):
    async def probe(self, message: str) -> ProbeResult:
        if message == "fail-me":
            return ProbeResult(success=False, error="boom")
        return ProbeResult(success=True, latency_ms=42)

    async def run_task(self, prompt: str) -> TaskResult:
        return TaskResult(success=True, text=f"echo: {prompt}")


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


def test_add_account_rejects_unknown_provider(session_factory):
    with session_factory() as session:
        with pytest.raises(UnknownProviderError):
            add_account(session, provider="totally-unknown", label="x", api_key="k")


def test_set_account_proxy_valid(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        set_account_proxy(session, acc, "socks5://127.0.0.1:1080")
        assert acc.proxy_url == "socks5://127.0.0.1:1080"


def test_set_account_proxy_clears_with_none(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        set_account_proxy(session, acc, "http://proxy:8080")
        set_account_proxy(session, acc, None)
        assert acc.proxy_url is None


def test_set_account_proxy_rejects_garbage(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        with pytest.raises(InvalidProxyError):
            set_account_proxy(session, acc, "not-a-url")


def test_set_account_enabled_toggle(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        assert acc.enabled is True
        set_account_enabled(session, acc, False)
        assert acc.enabled is False


def test_delete_account_removes_it_and_its_schedules(session_factory, account):
    with session_factory() as session:
        acc = session.get(AIAccount, account.id)
        add_schedule(session, account=acc, time_of_day="09:00")
        delete_account(session, acc)
        assert session.get(AIAccount, account.id) is None


def test_get_account_by_label(session_factory, account):
    with session_factory() as session:
        found = get_account_by_label(session, account.label)
        assert found.id == account.id
        assert get_account_by_label(session, "does-not-exist") is None
