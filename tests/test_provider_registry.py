from __future__ import annotations

import pytest

from app.db.models import ProviderAccountStatus, ProviderName, TaskType
from app.providers.base import AIProvider, AuthStatus, ProviderResult, RunOptions
from app.providers.registry import ProviderRegistry
from app.providers.router import NoProviderAvailableError, pick_provider


class FakeProvider(AIProvider):
    def __init__(self, name: ProviderName, *, connected: bool) -> None:
        self.name = name
        self._connected = connected

    def auth_status(self) -> AuthStatus:
        status = ProviderAccountStatus.CONNECTED if self._connected else ProviderAccountStatus.NOT_CONNECTED
        return AuthStatus(status=status)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        return ProviderResult(text="ok")


def test_disabled_provider_excluded_from_connected():
    registry = ProviderRegistry({ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=True)})
    assert registry.connected() == [ProviderName.CLAUDE]

    registry.disable(ProviderName.CLAUDE)
    assert registry.connected() == []
    assert registry.is_disabled(ProviderName.CLAUDE) is True


def test_enable_restores_visibility():
    registry = ProviderRegistry({ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=True)})
    registry.disable(ProviderName.CLAUDE)
    registry.enable(ProviderName.CLAUDE)

    assert registry.connected() == [ProviderName.CLAUDE]
    assert registry.is_disabled(ProviderName.CLAUDE) is False


def test_disable_does_not_touch_underlying_credentials():
    """Отключение — мягкое, на уровне реестра; сам провайдер (и его
    настоящий auth_status) не меняется."""
    provider = FakeProvider(ProviderName.CLAUDE, connected=True)
    registry = ProviderRegistry({ProviderName.CLAUDE: provider})

    registry.disable(ProviderName.CLAUDE)

    assert provider.auth_status().status == ProviderAccountStatus.CONNECTED


def test_router_skips_disabled_provider():
    registry = ProviderRegistry(
        {
            ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=True),
            ProviderName.CODEX: FakeProvider(ProviderName.CODEX, connected=True),
        }
    )
    registry.disable(ProviderName.CLAUDE)

    chosen = pick_provider(TaskType.CHECK_FULL, registry)
    assert chosen == ProviderName.CODEX


def test_router_raises_if_only_provider_disabled():
    registry = ProviderRegistry({ProviderName.CLAUDE: FakeProvider(ProviderName.CLAUDE, connected=True)})
    registry.disable(ProviderName.CLAUDE)

    with pytest.raises(NoProviderAvailableError):
        pick_provider(TaskType.CHECK_FULL, registry)
