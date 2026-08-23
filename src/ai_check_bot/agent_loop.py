"""Generic tool-use agent loop: calls a model, executes any tool_use requests against
agent_tools.py (sandboxed to one project root), feeds results back, repeats until the
model gives a final text answer or max_turns is hit.

Provider-agnostic by design: the actual model call is injected as `call_model`, so this
loop's turn-taking / tool-dispatch / read-only-enforcement logic is fully testable with a
plain Python fake — no live API calls needed. See providers/claude.py for the Anthropic-
specific adapter that builds a `call_model` from a real anthropic.AsyncAnthropic client.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from ai_check_bot import agent_tools as tools

TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read a file's full content, given a path relative to the project root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List project files matching a glob pattern (default: every file).",
        "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}},
    },
    {
        "name": "grep",
        "description": "Search file contents for a regex pattern, optionally limited to a glob.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "glob_pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace old_string with new_string in one file. old_string must match EXACTLY "
            "ONE place in the file, or this call fails — include enough surrounding context "
            "to make it unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": "Create a new file, or fully overwrite an existing one, with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
]

# CHEK_PROTOCOL.md's read-only roles (checkers, critics, verifier, planner, gap-finder,
# web-researcher) must never edit. Passing this as `allowed_tools` enforces it at the
# tool-dispatch layer — a prompt saying "you are read-only" is advisory; this makes an
# edit_file/write_file call actually fail instead of trusting the model to comply.
READ_ONLY_TOOLS = ["read_file", "list_files", "grep"]
ALL_TOOLS = ["read_file", "list_files", "grep", "edit_file", "write_file"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ModelTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: list[dict] = field(default_factory=list)  # exact content blocks, appended
    # verbatim as the assistant turn — avoids hand-reconstructing the API's block format.


@dataclass(frozen=True)
class AgentResult:
    final_text: str
    turns_used: int
    hit_turn_limit: bool
    tool_calls_made: int


class ToolNotAllowedError(Exception):
    pass


def _dispatch_tool(root: Path, name: str, tool_input: dict, allowed_tools: list[str]) -> str:
    if name not in allowed_tools:
        raise ToolNotAllowedError(f"tool '{name}' is not permitted for this role")
    if name == "read_file":
        return tools.read_file(root, tool_input["path"])
    if name == "list_files":
        return "\n".join(tools.list_files(root, tool_input.get("pattern", "**/*"))) or "(no files matched)"
    if name == "grep":
        hits = tools.grep(root, tool_input["pattern"], glob_pattern=tool_input.get("glob_pattern", "**/*"))
        return "\n".join(f"{h.path}:{h.line_no}: {h.line}" for h in hits) or "(no matches)"
    if name == "edit_file":
        tools.edit_file(root, tool_input["path"], tool_input["old_string"], tool_input["new_string"])
        return "ok"
    if name == "write_file":
        tools.write_file(root, tool_input["path"], tool_input["content"])
        return "ok"
    raise ToolNotAllowedError(f"unknown tool '{name}'")  # unreachable given TOOL_SCHEMAS; defensive


async def run_agent_loop(
    call_model: Callable[[str, list[dict]], Awaitable[ModelTurn]],
    root: Path,
    system_prompt: str,
    user_prompt: str,
    *,
    allowed_tools: list[str] | None = None,
    max_turns: int = 20,
) -> AgentResult:
    allowed = ALL_TOOLS if allowed_tools is None else allowed_tools
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    tool_calls_made = 0
    last_turn: ModelTurn | None = None

    for turn in range(1, max_turns + 1):
        last_turn = await call_model(system_prompt, messages)

        if not last_turn.tool_calls:
            return AgentResult(
                final_text=last_turn.text, turns_used=turn, hit_turn_limit=False, tool_calls_made=tool_calls_made
            )

        messages.append({"role": "assistant", "content": last_turn.raw_content})
        tool_results = []
        for call in last_turn.tool_calls:
            tool_calls_made += 1
            try:
                output = _dispatch_tool(root, call.name, call.input, allowed)
                tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": output})
            except Exception as exc:
                # Broad on purpose: a bad path, an ambiguous edit, a missing required
                # input key — any of these are NORMAL outcomes of AI-driven tool calls
                # against unpredictable model output, and must come back to the model as
                # an error it can react to, not crash the whole run. Bugs in the tool
                # implementations themselves are caught by their own unit tests
                # (test_agent_tools.py), not hidden by this boundary.
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": f"ERROR: {exc}", "is_error": True}
                )
        messages.append({"role": "user", "content": tool_results})

    return AgentResult(
        final_text=last_turn.text if last_turn is not None else "",
        turns_used=max_turns,
        hit_turn_limit=True,
        tool_calls_made=tool_calls_made,
    )
