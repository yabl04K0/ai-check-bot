"""Claude (Anthropic) provider. Messages API is stateless — there is no server-side
conversation object to delete after probing, unlike e.g. an Assistants-style thread API."""
from __future__ import annotations

import time
from pathlib import Path

import anthropic
import httpx

from ai_check_bot import agent_loop
from ai_check_bot.providers.base import AIProvider, ProbeResult, TaskResult

PROBE_MODEL = "claude-haiku-4-5-20251001"
TASK_MODEL = "claude-sonnet-4-5-20250929"
TASK_MAX_TOKENS = 4096
AGENT_MODEL = "claude-sonnet-4-5-20250929"
AGENT_MAX_TOKENS = 8192


def _response_to_model_turn(response) -> agent_loop.ModelTurn:
    """Converts a raw anthropic.types.Message into agent_loop's provider-agnostic
    ModelTurn. Standalone (not a closure) so it is unit-testable against a fake response
    object without a live API call — see tests/test_claude_provider.py."""
    text = "".join(block.text for block in response.content if block.type == "text")
    tool_calls = [
        agent_loop.ToolCall(id=block.id, name=block.name, input=block.input)
        for block in response.content
        if block.type == "tool_use"
    ]
    raw_content = [block.model_dump() for block in response.content]
    return agent_loop.ModelTurn(text=text, tool_calls=tool_calls, raw_content=raw_content)


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

    async def run_agentic_task(
        self,
        root: Path,
        system_prompt: str,
        user_prompt: str,
        *,
        allowed_tools: list[str] | None = None,
        max_turns: int = 20,
    ) -> agent_loop.AgentResult:
        """Provider-specific extension, not part of the AIProvider ABC: the tool-use
        agent loop (agent_loop.py) that CHEK_PROTOCOL.md's Steps 5-12 roles run through.
        Deliberately not forced onto every provider — its shape (a project root, a tool
        allowlist, a turn budget) is very different from probe()/run_task(), and a future
        provider without tool-use support genuinely cannot offer this, not just "hasn't
        implemented it yet". Callers check for the method rather than assuming it exists."""
        client = self._client()

        async def call_model(system: str, messages: list[dict]) -> agent_loop.ModelTurn:
            response = await client.messages.create(
                model=AGENT_MODEL,
                max_tokens=AGENT_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=agent_loop.TOOL_SCHEMAS,
            )
            return _response_to_model_turn(response)

        try:
            return await agent_loop.run_agent_loop(
                call_model, root, system_prompt, user_prompt, allowed_tools=allowed_tools, max_turns=max_turns
            )
        finally:
            await client.close()
