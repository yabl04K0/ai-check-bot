"""Claude (Anthropic) provider. Messages API is stateless — there is no server-side
conversation object to delete after probing, unlike e.g. an Assistants-style thread API."""
from __future__ import annotations

import time

import anthropic
import httpx

from ai_check_bot.providers.base import AIProvider, ProbeResult, TaskResult

PROBE_MODEL = "claude-haiku-4-5-20251001"
TASK_MODEL = "claude-sonnet-4-5-20250929"
TASK_MAX_TOKENS = 4096


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

    async def run_task(self, prompt: str) -> TaskResult:
        client = self._client()
        try:
            response = await client.messages.create(
                model=TASK_MODEL,
                max_tokens=TASK_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            return TaskResult(success=False, error=str(exc))
        finally:
            # asyncio.CancelledError (task.cancel() from the jobs.py cancel button)
            # passes through this finally and still closes the client, then re-raises —
            # do NOT catch it above, only anthropic.APIError.
            await client.close()
        text = "".join(block.text for block in response.content if block.type == "text")
        return TaskResult(success=True, text=text)
