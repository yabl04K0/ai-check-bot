import pytest

from ai_check_bot import agent_loop as loop


def _fake_model(turns):
    """Returns a call_model callable that yields the given ModelTurn objects in order,
    one per call, and records every `messages` list it was invoked with."""
    calls = []
    it = iter(turns)

    async def call_model(system_prompt, messages):
        calls.append({"system": system_prompt, "messages": [dict(m) for m in messages]})
        return next(it)

    call_model.calls = calls
    return call_model


def _text_turn(text: str) -> loop.ModelTurn:
    return loop.ModelTurn(text=text, tool_calls=[], raw_content=[{"type": "text", "text": text}])


def _tool_turn(*calls: loop.ToolCall) -> loop.ModelTurn:
    raw = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    return loop.ModelTurn(text="", tool_calls=list(calls), raw_content=raw)


async def test_single_turn_no_tools(tmp_path):
    model = _fake_model([_text_turn("all good")])
    result = await loop.run_agent_loop(model, tmp_path, "sys", "check this")
    assert result.final_text == "all good"
    assert result.turns_used == 1
    assert result.hit_turn_limit is False
    assert result.tool_calls_made == 0


async def test_two_turn_with_real_tool_call(tmp_path):
    (tmp_path / "a.py").write_text("x = 42\n")
    model = _fake_model(
        [
            _tool_turn(loop.ToolCall(id="t1", name="read_file", input={"path": "a.py"})),
            _text_turn("the file sets x to 42"),
        ]
    )
    result = await loop.run_agent_loop(model, tmp_path, "sys", "read a.py")
    assert result.final_text == "the file sets x to 42"
    assert result.turns_used == 2
    assert result.tool_calls_made == 1

    # turn 2 must have seen the REAL file content as the tool result, not a stub
    second_call_messages = model.calls[1]["messages"]
    tool_result_msg = second_call_messages[-1]
    assert tool_result_msg["role"] == "user"
    assert "x = 42" in tool_result_msg["content"][0]["content"]


async def test_multiple_tool_calls_in_one_turn(tmp_path):
    (tmp_path / "a.py").write_text("A\n")
    (tmp_path / "b.py").write_text("B\n")
    model = _fake_model(
        [
            _tool_turn(
                loop.ToolCall(id="t1", name="read_file", input={"path": "a.py"}),
                loop.ToolCall(id="t2", name="read_file", input={"path": "b.py"}),
            ),
            _text_turn("read both"),
        ]
    )
    result = await loop.run_agent_loop(model, tmp_path, "sys", "read both files")
    assert result.tool_calls_made == 2
    tool_results = model.calls[1]["messages"][-1]["content"]
    assert len(tool_results) == 2
    assert tool_results[0]["tool_use_id"] == "t1"
    assert tool_results[1]["tool_use_id"] == "t2"


async def test_disallowed_tool_returns_error_result_not_raise(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    model = _fake_model(
        [
            _tool_turn(loop.ToolCall(id="t1", name="edit_file", input={"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})),
            _text_turn("done"),
        ]
    )
    result = await loop.run_agent_loop(model, tmp_path, "sys", "edit it", allowed_tools=loop.READ_ONLY_TOOLS)
    assert result.tool_calls_made == 1
    # the file must be UNTOUCHED — a read-only role's edit attempt must not go through
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    tool_result = model.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "not permitted" in tool_result["content"]


async def test_tool_exception_becomes_error_result_not_raise(tmp_path):
    model = _fake_model(
        [
            _tool_turn(loop.ToolCall(id="t1", name="read_file", input={"path": "missing.py"})),
            _text_turn("could not read it"),
        ]
    )
    result = await loop.run_agent_loop(model, tmp_path, "sys", "read missing.py")
    assert result.final_text == "could not read it"
    tool_result = model.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True


async def test_max_turns_exceeded_reports_hit_limit(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    endless = [_tool_turn(loop.ToolCall(id=f"t{i}", name="read_file", input={"path": "a.py"})) for i in range(5)]
    model = _fake_model(endless)
    result = await loop.run_agent_loop(model, tmp_path, "sys", "loop forever", max_turns=5)
    assert result.hit_turn_limit is True
    assert result.turns_used == 5
    assert result.tool_calls_made == 5


async def test_allowed_tools_defaults_to_all(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    model = _fake_model(
        [
            _tool_turn(loop.ToolCall(id="t1", name="edit_file", input={"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})),
            _text_turn("edited"),
        ]
    )
    await loop.run_agent_loop(model, tmp_path, "sys", "edit it")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
