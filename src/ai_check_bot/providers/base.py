"""AIProvider interface. Every provider-specific SDK call in this project goes through a
subclass of this — see CLAUDE.md "CRITICAL: provider abstraction"."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    latency_ms: int | None = None
    error: str | None = None


class AIProvider(ABC):
    """One connected account of one AI provider. `proxy_url` is optional and per-account,
    not global — each account may sit behind its own egress proxy."""

    def __init__(self, api_key: str, proxy_url: str | None = None) -> None:
        self.api_key = api_key
        self.proxy_url = proxy_url

    @abstractmethod
    async def probe(self, message: str) -> ProbeResult:
        """Verify the account works: exchange one round-trip message, and clean up any
        server-side conversation/session resource the call created. For a stateless
        provider API (no server-side thread object) cleanup is a no-op — that is correct,
        not a shortcut; each subclass documents which case it is."""
        raise NotImplementedError
