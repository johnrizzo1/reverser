"""Tests for execute_tool allowlist enforcement (closes the 43-http_request bug)."""

import asyncio


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_execute_tool_rejects_name_outside_allowlist():
    """Tool name not in allowed_set returns an error result without dispatching."""
    from reverser.backends.tools import execute_tool

    async def fake_handler(args):
        raise AssertionError("handler should NOT have been called")

    handlers = {"http_request": fake_handler}
    allowed_set = {"nmap_scan", "kb_show"}  # http_request NOT in set

    result_text, is_error = _run(
        execute_tool(handlers, "http_request", "{}", allowed_set=allowed_set)
    )
    assert is_error is True
    assert "not in this profile's allowlist" in result_text
    assert "nmap_scan" in result_text or "kb_show" in result_text


def test_execute_tool_passes_through_when_no_allowlist():
    """Default (allowed_set=None) preserves existing behavior."""
    from reverser.backends.tools import execute_tool

    async def fake_handler(args):
        return {"content": [{"type": "text", "text": "ran"}]}

    handlers = {"some_tool": fake_handler}
    result_text, is_error = _run(
        execute_tool(handlers, "some_tool", "{}", allowed_set=None)
    )
    assert is_error is False
    assert "ran" in result_text


def test_execute_tool_error_message_lists_allowed_alternatives():
    """The error tells the agent what tools ARE available."""
    from reverser.backends.tools import execute_tool

    async def h(args): return {"content": []}
    handlers = {"a": h, "b": h, "c": h}
    allowed_set = {"a", "b"}

    result_text, _ = _run(
        execute_tool(handlers, "c", "{}", allowed_set=allowed_set)
    )
    assert "a" in result_text
    assert "b" in result_text


def test_execute_tool_truncates_very_long_allowlists():
    """When the allowlist has >20 tools, the error caps the listing and adds 'and N others'."""
    from reverser.backends.tools import execute_tool

    async def h(args): return {"content": []}
    handlers = {f"tool_{i}": h for i in range(50)}
    allowed_set = {f"tool_{i}" for i in range(50)}

    result_text, _ = _run(
        execute_tool(handlers, "out_of_set", "{}", allowed_set=allowed_set)
    )
    assert "and 30 others" in result_text  # 50 - 20 = 30


def test_execute_tool_allowed_passes_through():
    """A tool name that IS in the allowlist runs normally."""
    from reverser.backends.tools import execute_tool

    async def fake_handler(args):
        return {"content": [{"type": "text", "text": "success"}]}

    handlers = {"allowed_tool": fake_handler}
    allowed_set = {"allowed_tool", "other_tool"}

    result_text, is_error = _run(
        execute_tool(handlers, "allowed_tool", "{}", allowed_set=allowed_set)
    )
    assert is_error is False
    assert "success" in result_text
