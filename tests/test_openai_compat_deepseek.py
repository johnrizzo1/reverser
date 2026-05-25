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
