import pytest

from ai_check_bot.models import AIAccount
from ai_check_bot.providers.base import AIProvider, ProbeResult, TaskResult
from ai_check_bot.providers.registry import PROVIDER_REGISTRY
from ai_check_bot.task_service import NoAccountAvailableError, run_custom_task


class FakeTaskProvider(AIProvider):
    async def probe(self, message: str) -> ProbeResult:
        return ProbeResult(success=True, latency_ms=1)

    async def run_task(self, prompt: str) -> TaskResult:
        return TaskResult(success=True, text=f"echo: {prompt}")


@pytest.fixture(autouse=True)
def register_fake_task_provider():
    PROVIDER_REGISTRY["fake-task"] = FakeTaskProvider
    yield
    del PROVIDER_REGISTRY["fake-task"]


def _account(session_factory, label, provider="fake-task", enabled=True):
    with session_factory() as session:
        acc = AIAccount(provider=provider, label=label, api_key="k", enabled=enabled)
        session.add(acc)
        session.commit()


async def test_run_custom_task_returns_label_and_result(session_factory):
    _account(session_factory, "only-one")
    label, result = await run_custom_task(session_factory, "fake-task", "hello")
    assert label == "only-one"
    assert result.success is True
    assert result.text == "echo: hello"


async def test_run_custom_task_no_account_raises(session_factory):
    with pytest.raises(NoAccountAvailableError):
        await run_custom_task(session_factory, "fake-task", "hello")


async def test_run_custom_task_skips_disabled(session_factory):
    _account(session_factory, "off", enabled=False)
    with pytest.raises(NoAccountAvailableError):
        await run_custom_task(session_factory, "fake-task", "hello")
