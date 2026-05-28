"""DeepSeek-family support in OpenAICompatBackend.

Coder-V2-Lite has no native tool-call template, so we teach it the
<tool_call>{...}</tool_call> JSON format via a system-prompt preamble.
The existing _JSON_TOOL_PATTERNS already parses that format, so no new
parser is needed.
"""
import pytest

from reverser.backends.openai_compat import _is_deepseek_family


@pytest.mark.parametrize("name", [
    "deepseek-coder-v2-lite-instruct",
    "DeepSeek-V2",
    "deepseek-r1:7b",
    "lmstudio-community/DeepSeek-Coder-V2-Lite-Instruct-GGUF",
])
def test_is_deepseek_family_true(name):
    assert _is_deepseek_family(name) is True


@pytest.mark.parametrize("name", [
    "qwen3.5-coder",
    "gemma-3-27b",
    "llama-3.3",
    "",
    None,
])
def test_is_deepseek_family_false(name):
    assert _is_deepseek_family(name) is False


from reverser.backends.openai_compat import _build_deepseek_tools_preamble


def _fake_tool(name, description, params_schema):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params_schema,
        },
    }


def test_preamble_lists_each_tool_name_and_description():
    tools = [
        _fake_tool("nmap_scan", "Run an nmap scan",
                   {"type": "object", "properties": {"target": {"type": "string"}}}),
        _fake_tool("ghidra_decompile", "Decompile a function",
                   {"type": "object", "properties": {"addr": {"type": "string"}}}),
    ]
    preamble = _build_deepseek_tools_preamble(tools)
    assert "nmap_scan" in preamble
    assert "Run an nmap scan" in preamble
    assert "ghidra_decompile" in preamble
    assert "Decompile a function" in preamble


def test_preamble_contains_wire_format_markers():
    tools = [_fake_tool("bash", "Run a shell command",
                        {"type": "object", "properties": {"cmd": {"type": "string"}}})]
    preamble = _build_deepseek_tools_preamble(tools)
    assert "<tool_call>" in preamble
    assert "</tool_call>" in preamble


def test_preamble_includes_parameter_schema():
    tools = [_fake_tool("bash", "Run a shell command",
                        {"type": "object", "properties": {"cmd": {"type": "string"}}})]
    preamble = _build_deepseek_tools_preamble(tools)
    assert '"cmd"' in preamble
    assert '"type": "string"' in preamble or '"type":"string"' in preamble


def test_preamble_with_empty_tools_returns_empty_string():
    """Defensive: call site already guards on tools_for_model, but the
    function itself should be lenient and return an empty string for [].
    """
    assert _build_deepseek_tools_preamble([]) == ""


from reverser.backends.openai_compat import _extract_text_tool_calls


def test_preamble_format_is_parseable_by_existing_extractor():
    """The wire format in the preamble must match what the existing
    text-tool-call extractor parses. This pins that invariant.
    """
    tools = [_fake_tool("nmap_scan", "Run nmap",
                        {"type": "object", "properties": {"target": {"type": "string"}}})]
    preamble = _build_deepseek_tools_preamble(tools)
    assert '<tool_call>{"name": "TOOL_NAME"' in preamble
    assistant_msg = (
        "I'll scan now.\n"
        '<tool_call>{"name": "nmap_scan", "arguments": {"target": "10.0.0.1"}}</tool_call>\n'
        "Standing by for results."
    )
    calls = _extract_text_tool_calls(assistant_msg, {"nmap_scan"})
    assert len(calls) == 1
    name, args_json = calls[0]
    assert name == "nmap_scan"
    import json as _json
    assert _json.loads(args_json) == {"target": "10.0.0.1"}


def test_unknown_tool_is_rejected_by_extractor():
    """Defense in depth — even if the model invents a tool, we don't run it."""
    assistant_msg = (
        '<tool_call>{"name": "rm_rf_slash", "arguments": {}}</tool_call>'
    )
    calls = _extract_text_tool_calls(assistant_msg, {"nmap_scan"})
    assert calls == []


def test_wrapped_call_does_not_double_fire():
    """Regression: a <tool_call>{...}</tool_call> block must only produce
    one tool call, not two (one for the wrapped pattern + one for the bare
    JSON inside it). Order of _JSON_TOOL_PATTERNS matters.
    """
    assistant_msg = (
        '<tool_call>{"name": "bash", "arguments": {"cmd": "ls"}}</tool_call>'
    )
    calls = _extract_text_tool_calls(assistant_msg, {"bash"})
    assert len(calls) == 1


def test_bare_json_still_matches_when_unwrapped():
    """Make sure the reorder didn't break bare-JSON extraction for models
    that don't use the wrapper (this is what test_openai_backend_ids.py
    exercises via the full backend run).
    """
    assistant_msg = (
        '{"name": "bash", "arguments": {"cmd": "ls"}}\n'
        '{"name": "bash", "arguments": {"cmd": "pwd"}}'
    )
    calls = _extract_text_tool_calls(assistant_msg, {"bash"})
    assert len(calls) == 2


from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _mk_response(content, tool_calls=None, finish_reason="stop"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.role = "assistant"
    msg.model_dump = lambda: {"reasoning": None}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)]
    )


@pytest.mark.asyncio
async def test_deepseek_preamble_is_appended_to_system_prompt(monkeypatch):
    """When family is deepseek and tools are present, the preamble is
    appended to the system prompt sent on the chat-completions request.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="deepseek-coder-v2-lite-instruct", api_key="x",
    )
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command",
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    create_mock.assert_called_once()
    sent_messages = create_mock.call_args.kwargs["messages"]
    system_msg = sent_messages[0]
    assert system_msg["role"] == "system"
    assert system_msg["content"].startswith("be helpful")
    assert "<tool_call>" in system_msg["content"]
    assert "bash" in system_msg["content"]


@pytest.mark.asyncio
async def test_generic_family_does_not_get_preamble(monkeypatch):
    """A non-DeepSeek model must not get the preamble (no regression for
    Qwen3/Gemma/etc., which have their own paths).
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(tools=[], model="qwen3-coder", api_key="x")
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "bash", "description": "x", "parameters": {}},
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    sent_messages = create_mock.call_args.kwargs["messages"]
    assert sent_messages[0]["content"] == "be helpful"
    assert "<tool_call>" not in sent_messages[0]["content"]


@pytest.mark.asyncio
async def test_model_family_override_forces_deepseek(monkeypatch):
    """model_family='deepseek' forces preamble even when the model name
    doesn't say 'deepseek'.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="custom-finetune-tag", api_key="x",
        model_family="deepseek",
    )
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "bash", "description": "x", "parameters": {}},
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    assert "<tool_call>" in create_mock.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_model_family_override_forces_generic(monkeypatch):
    """And model_family='generic' suppresses the preamble even on
    deepseek-named models.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="deepseek-coder-v2-lite-instruct", api_key="x",
        model_family="generic",
    )
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "bash", "description": "x", "parameters": {}},
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    assert "<tool_call>" not in create_mock.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_json_fence_tool_call_is_stripped_from_visible_text():
    """Regression: DeepSeek-Coder-V2 emits tool calls as ```json {...}```
    fences (despite the preamble asking for <tool_call>...</tool_call>).
    The extractor executes them, but the scrubber must also remove them
    from the text the user sees — otherwise the raw JSON leaks into chat.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="deepseek-coder-v2-lite-instruct", api_key="x",
    )
    backend._handlers = {"nmap_scan": AsyncMock(return_value={
        "content": [{"type": "text", "text": "Host up"}], "is_error": False,
    })}
    backend._tool_names = {"nmap_scan"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "nmap_scan", "description": "scan",
                     "parameters": {"type": "object",
                                    "properties": {"target": {"type": "string"}}}},
    }]

    fenced = (
        "First, let's perform a basic reconnaissance scan:\n\n"
        "```json\n"
        '{\n  "name": "nmap_scan",\n  "arguments": {\n    "target": "10.0.0.1"\n  }\n}\n'
        "```"
    )
    # Two responses: turn 1 emits the fenced call; turn 2 emits a plain final.
    create_mock = AsyncMock(side_effect=[_mk_response(fenced), _mk_response("done")])
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    events = []
    async for ev in backend.run(prompt="hi", system_prompt="be helpful", max_turns=2):
        events.append(ev)

    text_events = [e for e in events if e.kind == "text"]
    tool_calls = [e for e in events if e.kind == "tool_call"]

    # Tool was executed
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "nmap_scan"

    # No emitted text event contains the raw JSON fence
    for ev in text_events:
        assert "```json" not in ev.content, (
            f"JSON fence leaked into visible text: {ev.content!r}"
        )
        assert '"name": "nmap_scan"' not in ev.content, (
            f"Tool-call JSON leaked into visible text: {ev.content!r}"
        )

    # The natural-language reasoning before the fence is preserved
    reasoning_events = [e for e in text_events
                        if "reconnaissance scan" in e.content]
    assert reasoning_events, (
        f"Reasoning prose was stripped along with the fence; text events: "
        f"{[e.content for e in text_events]!r}"
    )


@pytest.mark.asyncio
async def test_json_array_fence_tool_calls_are_stripped_from_visible_text():
    """Regression: DeepSeek-Coder-V2 batches parallel tool calls inside a
    ```json [ {...}, {...} ] ``` array. The bare-JSON extractor finds each
    inner object and executes them; the scrubber must remove the entire
    fence so the array doesn't leak into chat. Without this, every
    multi-tool turn dumps raw JSON to the user.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="deepseek-coder-v2-lite-instruct", api_key="x",
    )
    backend._handlers = {
        "nmap_scan": AsyncMock(return_value={
            "content": [{"type": "text", "text": "no hosts up"}], "is_error": False,
        }),
        "whatweb_scan": AsyncMock(return_value={
            "content": [{"type": "text", "text": "nothing"}], "is_error": False,
        }),
    }
    backend._tool_names = {"nmap_scan", "whatweb_scan"}
    backend._openai_tools = [
        {"type": "function", "function": {"name": "nmap_scan", "description": "x",
            "parameters": {"type": "object",
                           "properties": {"target": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "whatweb_scan", "description": "x",
            "parameters": {"type": "object",
                           "properties": {"target": {"type": "string"}}}}},
    ]

    # The exact shape DeepSeek emits when batching parallel calls
    fenced_array = (
        "```json\n"
        "[\n"
        "  {\n"
        '    "name": "nmap_scan",\n'
        '    "arguments": {"target": "DevArea"}\n'
        "  },\n"
        "  {\n"
        '    "name": "whatweb_scan",\n'
        '    "arguments": {"target": "DevArea"}\n'
        "  }\n"
        "]\n"
        "```"
    )
    create_mock = AsyncMock(side_effect=[
        _mk_response(fenced_array),
        _mk_response("done"),
    ])
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    events = []
    async for ev in backend.run(prompt="hi", system_prompt="be helpful", max_turns=2):
        events.append(ev)

    text_events = [e for e in events if e.kind == "text"]
    tool_calls = [e for e in events if e.kind == "tool_call"]

    # Both tools in the array were executed
    assert {tc.tool_name for tc in tool_calls} == {"nmap_scan", "whatweb_scan"}, [
        tc.tool_name for tc in tool_calls
    ]

    # No emitted text event leaks the JSON array, the array brackets, or
    # the raw tool-call JSON
    for ev in text_events:
        assert "```json" not in ev.content, (
            f"JSON fence leaked into visible text: {ev.content!r}"
        )
        assert '"name":' not in ev.content, (
            f"Tool-call JSON leaked into visible text: {ev.content!r}"
        )


def test_scrubber_preserves_unrelated_json_in_code_fences():
    """The scrubber must only strip fences carrying tool-call shape.
    A config example or schema dump in a ```json``` block must survive.
    """
    from reverser.backends.openai_compat import _TOOL_CALL_FENCE_SCRUB

    config = (
        "Here is the DB config:\n\n"
        "```json\n"
        '{"host": "localhost", "port": 5432}\n'
        "```"
    )
    scrubbed = _TOOL_CALL_FENCE_SCRUB.sub("", config).strip()
    assert "```json" in scrubbed, scrubbed
    assert '"host"' in scrubbed, scrubbed
