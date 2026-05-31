"""Tests for the dispatch idle-timeout watchdog primitive."""
import asyncio
import pytest

from reverser.tools.dispatch import (
    _DispatchStalled,
    _aiter_with_idle_timeout,
    _dispatch_idle_timeout,
)


async def _collect(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


async def test_passes_items_through_when_fast():
    async def fast():
        for i in range(3):
            yield i
    result = await _collect(_aiter_with_idle_timeout(fast(), 1.0))
    assert result == [0, 1, 2]


async def test_raises_stalled_when_idle_exceeds_window():
    closed = {"v": False}

    async def stalls():
        try:
            yield 0
            await asyncio.sleep(2)   # longer than the idle window
            yield 1                  # never reached
        finally:
            closed["v"] = True

    with pytest.raises(_DispatchStalled):
        await _collect(_aiter_with_idle_timeout(stalls(), 0.2))
    assert closed["v"] is True       # watchdog closed the stalled generator


async def test_empty_generator_yields_nothing_and_does_not_raise():
    async def empty():
        return
        yield  # pragma: no cover  (makes this an async generator)
    result = await _collect(_aiter_with_idle_timeout(empty(), 1.0))
    assert result == []


def test_dispatch_stalled_exposes_idle_seconds():
    exc = _DispatchStalled(0.2)
    assert exc.idle_seconds == 0.2
    assert "0.2" in str(exc)


def test_idle_timeout_reads_env(monkeypatch):
    monkeypatch.delenv("REVERSER_DISPATCH_IDLE_TIMEOUT", raising=False)
    assert _dispatch_idle_timeout() == 300.0
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "12.5")
    assert _dispatch_idle_timeout() == 12.5
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "garbage")
    assert _dispatch_idle_timeout() == 300.0


from unittest.mock import patch


def _call_tool(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def test_dispatch_times_out_on_stalled_specialist(monkeypatch, tmp_path):
    """A specialist whose generator stalls aborts with Status: timeout,
    clears in_flight, and returns a partial report instead of hanging."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "0.3")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def stalling_query(prompt, options):
        from claude_agent_sdk import AssistantMessage, TextBlock
        yield AssistantMessage(
            content=[TextBlock(text="Partial recon so far...")], model="claude",
        )
        await asyncio.sleep(5)   # never reaches a ResultMessage

    with patch("reverser.tools.dispatch.query", stalling_query):
        result = _call_tool(dispatch_specialist, {
            "specialty": "webrecon", "sub_goal": "enumerate",
            "target": "10.10.10.5", "hypothesis_id": 1,
        })

    # NOTE: adjust this extraction to the ACTUAL return shape you confirmed by
    # reading the end of dispatch_specialist. The line below handles a dict
    # envelope; if it returns a raw string, assert on `result` directly.
    body = result["content"][0]["text"] if isinstance(result, dict) and "content" in result else str(result)
    assert "timeout" in body.lower()
    assert "Partial recon so far" in body          # partial report preserved
    assert "READ THE REPORT BODY BELOW" in body    # advisory shown for timeout
    assert sess._snapshot.in_flight is None         # finally ran


from reverser.tools.dispatch import _dispatch_tool_timeout


def test_tool_timeout_reads_env(monkeypatch):
    monkeypatch.delenv("REVERSER_DISPATCH_TOOL_TIMEOUT", raising=False)
    assert _dispatch_tool_timeout() == 1800.0
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "42.5")
    assert _dispatch_tool_timeout() == 42.5
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "garbage")
    assert _dispatch_tool_timeout() == 1800.0


async def test_pending_tool_uses_tool_window_not_idle():
    """When a tool is pending, a gap shorter than tool_seconds but longer than
    idle_seconds must NOT raise — the long tool budget applies."""
    async def gen():
        yield 0
        await asyncio.sleep(0.4)   # > idle (0.2), < tool (2.0)
        yield 1

    result = await _collect(_aiter_with_idle_timeout(
        gen(), 0.2, tool_seconds=2.0, is_tool_pending=lambda: True,
    ))
    assert result == [0, 1]


async def test_no_pending_tool_uses_idle_window():
    """When no tool is pending, the short idle window applies and a long gap raises."""
    async def gen():
        yield 0
        await asyncio.sleep(2)     # > idle (0.2)
        yield 1

    with pytest.raises(_DispatchStalled):
        await _collect(_aiter_with_idle_timeout(
            gen(), 0.2, tool_seconds=5.0, is_tool_pending=lambda: False,
        ))


def test_dispatch_does_not_abort_while_tool_pending(monkeypatch, tmp_path):
    """A specialist that emits a tool_call (no result yet) then pauses longer than the
    idle window but within the tool window must NOT be aborted."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "0.3")
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "5")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def tool_then_finish(prompt, options):
        from claude_agent_sdk import (
            AssistantMessage, TextBlock, ToolUseBlock, UserMessage,
            ToolResultBlock, ResultMessage,
        )
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="nmap", input={"target": "x"})], model="claude")
        await asyncio.sleep(0.6)   # > idle (0.3), < tool (5) — tool is pending
        yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="open: 80", is_error=False)])
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")], model="claude")
        yield ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0,
                            is_error=False, num_turns=2, session_id="t",
                            total_cost_usd=0.0, result="x")

    with patch("reverser.tools.dispatch.query", tool_then_finish):
        result = _call_tool(dispatch_specialist, {
            "specialty": "webrecon", "sub_goal": "enumerate",
            "target": "10.10.10.5", "hypothesis_id": 1,
        })
    body = result["content"][0]["text"] if isinstance(result, dict) and "content" in result else str(result)
    assert "timeout" not in body.lower()
    assert sess._snapshot.in_flight is None


def test_dispatch_aborts_when_idle_with_no_tool_pending(monkeypatch, tmp_path):
    """A specialist that emits final text (no tool pending) then stalls IS aborted at
    the short idle window."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "0.3")
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "30")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def text_then_hang(prompt, options):
        from claude_agent_sdk import AssistantMessage, TextBlock
        yield AssistantMessage(content=[TextBlock(text="done enumerating")], model="claude")
        await asyncio.sleep(2)     # no tool pending -> idle window (0.3s) applies

    with patch("reverser.tools.dispatch.query", text_then_hang):
        result = _call_tool(dispatch_specialist, {
            "specialty": "webrecon", "sub_goal": "enumerate",
            "target": "10.10.10.5", "hypothesis_id": 1,
        })
    body = result["content"][0]["text"] if isinstance(result, dict) and "content" in result else str(result)
    assert "timeout" in body.lower()
    assert sess._snapshot.in_flight is None
