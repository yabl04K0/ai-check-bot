"""Какие (provider, account_label) реально нуждаются в прокси — только
подключённые основные ключи + добавленные доп. аккаунты среди
проксируемых провайдеров (см. app/proxies/consumers.py::PROXIED_PROVIDERS)."""

from __future__ import annotations

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.accounts_store import add_extra_account
from app.providers.base import AuthStatus
from app.providers.registry import ProviderRegistry
from app.proxies.consumers import active_consumers
from app.proxies.pool import Consumer


class _FakeProvider:
    def __init__(self, name: ProviderName, *, connected: bool) -> None:
        self.name = name
        self._connected = connected

    def auth_status(self) -> AuthStatus:
        status = ProviderAccountStatus.CONNECTED if self._connected else ProviderAccountStatus.NOT_CONNECTED
        return AuthStatus(status=status)


def test_connected_proxied_provider_yields_primary_consumer(db):
    registry = ProviderRegistry({ProviderName.GEMINI: _FakeProvider(ProviderName.GEMINI, connected=True)})

    consumers = active_consumers(registry)

    assert Consumer(provider=ProviderName.GEMINI, account_label="primary") in consumers


def test_not_connected_provider_yields_no_primary_consumer(db):
    registry = ProviderRegistry({ProviderName.GEMINI: _FakeProvider(ProviderName.GEMINI, connected=False)})

    consumers = active_consumers(registry)

    assert consumers == []


def test_non_proxied_provider_is_excluded_even_if_connected(db):
    """Claude/Cursor/Codex/local_llm/claude_code_cli/groq — прокси в них
    ещё не проброшен (см. докстринг consumers.py), поэтому им не заводим
    назначения, которые никто бы не использовал."""
    registry = ProviderRegistry({ProviderName.CLAUDE: _FakeProvider(ProviderName.CLAUDE, connected=True)})

    assert active_consumers(registry) == []


def test_extra_accounts_yield_additional_consumers(db):
    add_extra_account(ProviderName.GEMINI, "secret-1")
    add_extra_account(ProviderName.GEMINI, "secret-2")
    registry = ProviderRegistry({ProviderName.GEMINI: _FakeProvider(ProviderName.GEMINI, connected=True)})

    consumers = active_consumers(registry)

    labels = {c.account_label for c in consumers}
    assert labels == {"primary", "extra:1", "extra:2"}
