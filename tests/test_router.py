from __future__ import annotations

import pytest

from app.db.models import ProviderAccountStatus, ProviderName, TaskType
from app.providers.base import AIProvider, AuthStatus, ProviderResult, QuotaEstimate, RunOptions
from app.providers.registry import ProviderRegistry
from app.providers.router import NoProviderAvailableError, pick_provider


class FakeProvider(AIProvider):
    def __init__(self, name: ProviderName, *, connected: bool, used_pct: float | None = None) -> None:
        self.name = name
        self._connected = connected
        self._used_pct = used_pct

    def auth_status(self) -> AuthStatus:
        status = ProviderAccountStatus.CONNECTED if self._connected else ProviderAccountStatus.NOT_CONNECTED
        return AuthStatus(status=status)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        return ProviderResult(text="ok")

    def estimate_quota(self) -> QuotaEstimate:
        return QuotaEstimate(used_pct=self._used_pct, hours_to_reset=None)


def _registry(**providers: AIProvider) -> ProviderRegistry:
    return ProviderRegistry(providers)


def test_picks_first_connected_in_priority_order():
    registry = _registry(
        **{
            ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=False),
            ProviderName.CODEX: FakeProvider(ProviderName.CODEX, connected=True),
            ProviderName.CURSOR: FakeProvider(ProviderName.CURSOR, connected=True),
        }
    )
    chosen = pick_provider(TaskType.CHECK_FULL, registry)
    assert chosen == ProviderName.CODEX  # Claude не подключен, Codex следующий в приоритете


def test_no_provider_available_raises():
    registry = _registry(
        **{ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=False)}
    )
    with pytest.raises(NoProviderAvailableError):
        pick_provider(TaskType.CHECK_FULL, registry)


def test_skips_provider_with_exhausted_quota():
    registry = _registry(
        **{
            ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=True, used_pct=99.0),
            ProviderName.CODEX: FakeProvider(ProviderName.CODEX, connected=True, used_pct=10.0),
        }
    )
    chosen = pick_provider(TaskType.CHECK_FULL, registry)
    assert chosen == ProviderName.CODEX


def test_task_type_independent_of_provider_choice():
    """Тип задачи и провайдер — независимые измерения: смена типа задачи
    не должна требовать смены кода роутера, только приоритетов."""
    registry = _registry(
        **{ProviderName.LOCAL_LLM: FakeProvider(ProviderName.LOCAL_LLM, connected=True)}
    )
    chosen = pick_provider(TaskType.CHECK_LITE, registry)
    assert chosen == ProviderName.LOCAL_LLM
