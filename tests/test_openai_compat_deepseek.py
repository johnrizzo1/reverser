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
