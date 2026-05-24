"""OpenAI-compat backend must populate tool_use_id and turn on events.

Regression test for the renderer's duplicate-key warning. When tool_call
events arrive without tool_use_id, the chat pane's per-turn ordering
collapses them onto the same React key and React warns about duplicate
children. This test asserts the backend emits unique ids for both
structured and text-extracted tool calls.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from reverser.backends.openai_compat import OpenAICompatBackend


def _mk_response(*, content, tool_calls=None, finish_reason="stop"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.role = "assistant"
    msg.model_dump = lambda: {"reasoning": None}
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_structured_tool_call_populates_tool_use_id_and_turn(monkeypatch):
    backend = OpenAICompatBackend(tools=[], model="qwen3", api_key="x")
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{"type": "function", "function": {"name": "bash"}}]
    monkeypatch.setattr(
        "reverser.backends.openai_compat.execute_tool",
        AsyncMock(return_value=("ok", False)),
    )

    structured_call = SimpleNamespace(
        id="call_abc",
        function=SimpleNamespace(name="bash", arguments='{"cmd":"ls"}'),
    )
    response_with_tool = _mk_response(content="", tool_calls=[structured_call])
    response_done = _mk_response(content="all done", tool_calls=None)

    backend._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=[response_with_tool, response_done]),
            ),
        ),
    )

    events = []
    async for ev in backend.run(prompt="hi", system_prompt="sys", max_turns=3):
        events.append(ev)
        if ev.kind == "result":
            break

    tool_calls = [e for e in events if e.kind == "tool_call"]
    tool_results = [e for e in events if e.kind == "tool_result"]
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_use_id == "call_abc"
    assert tool_calls[0].turn == 1
    assert tool_results[0].tool_use_id == "call_abc"
    assert tool_results[0].turn == 1


@pytest.mark.asyncio
async def test_text_extracted_tool_calls_get_unique_synthetic_ids(monkeypatch):
    """Two text-extracted tool calls in one turn must not collide on id."""
    backend = OpenAICompatBackend(tools=[], model="qwen3", api_key="x")
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{"type": "function", "function": {"name": "bash"}}]
    monkeypatch.setattr(
        "reverser.backends.openai_compat.execute_tool",
        AsyncMock(return_value=("ok", False)),
    )

    # Model emits two text-embedded JSON tool calls in one assistant message.
    text_content = (
        '{"name": "bash", "arguments": {"cmd": "ls"}}\n'
        '{"name": "bash", "arguments": {"cmd": "pwd"}}'
    )
    response_with_text = _mk_response(content=text_content, tool_calls=None)
    response_done = _mk_response(content="all done", tool_calls=None)
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=[response_with_text, response_done]),
            ),
        ),
    )

    events = []
    async for ev in backend.run(prompt="hi", system_prompt="sys", max_turns=3):
        events.append(ev)
        if ev.kind == "result":
            break

    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert len(tool_calls) == 2
    ids = [e.tool_use_id for e in tool_calls]
    assert all(ids)            # no empty strings
    assert len(set(ids)) == 2  # all unique
    assert all(e.turn == 1 for e in tool_calls)
