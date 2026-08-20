"""Claude (Anthropic) provider. Messages API is stateless — there is no server-side
conversation object to delete after probing, unlike e.g. an Assistants-style thread API."""
from __future__ import annotations

import time

import anthropic
import httpx

from ai_check_bot.providers.base import AIProvider, ProbeResult

PROBE_MODEL = "claude-haiku-4-5-20251001"


class ClaudeProvider(AIProvider):
    def _client(self) -> anthropic.AsyncAnthropic:
        http_client = httpx.AsyncClient(proxy=self.proxy_url) if self.proxy_url else None
        return anthropic.AsyncAnthropic(api_key=self.api_key, http_client=http_client)

    async def probe(self, message: str) -> ProbeResult:
        client = self._client()
        started = time.monotonic()
        try:
            await client.messages.create(
                model=PROBE_MODEL,
                max_tokens=16,
                messages=[{"role": "user", "content": message}],
            )
        except anthropic.APIError as exc:
            return ProbeResult(success=False, error=str(exc))
        finally:
            await client.close()
        return ProbeResult(success=True, latency_ms=int((time.monotonic() - started) * 1000))
