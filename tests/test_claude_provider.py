from types import SimpleNamespace

from ai_check_bot.providers.claude import _response_to_model_turn


def _text_block(text):
    return SimpleNamespace(type="text", text=text, model_dump=lambda: {"type": "text", "text": text})


def _tool_use_block(id_, name, input_):
    return SimpleNamespace(
        type="tool_use",
        id=id_,
        name=name,
        input=input_,
        model_dump=lambda: {"type": "tool_use", "id": id_, "name": name, "input": input_},
    )


def test_text_only_response():
    response = SimpleNamespace(content=[_text_block("hello")])
    turn = _response_to_model_turn(response)
    assert turn.text == "hello"
    assert turn.tool_calls == []
    assert turn.raw_content == [{"type": "text", "text": "hello"}]


def test_tool_use_response_extracts_calls():
    response = SimpleNamespace(
        content=[
            _text_block("I'll check the file"),
            _tool_use_block("t1", "read_file", {"path": "a.py"}),
        ]
    )
    turn = _response_to_model_turn(response)
    assert turn.text == "I'll check the file"
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].id == "t1"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].input == {"path": "a.py"}
    assert len(turn.raw_content) == 2


def test_multiple_tool_use_blocks():
    response = SimpleNamespace(
        content=[
            _tool_use_block("t1", "read_file", {"path": "a.py"}),
            _tool_use_block("t2", "read_file", {"path": "b.py"}),
        ]
    )
    turn = _response_to_model_turn(response)
    assert [c.id for c in turn.tool_calls] == ["t1", "t2"]
    assert turn.text == ""
