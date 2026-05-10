"""Tests for dispatch_specialist in_flight tracking."""

import asyncio
from unittest.mock import patch


def _call_tool(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def test_dispatch_sets_in_flight_on_session_snapshot(monkeypatch, tmp_path):
    """When dispatch_specialist starts, it mutates session._snapshot.in_flight."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
    )
    current_session.set(sess)

    captured_in_flight_during_call = []

    async def capturing_query(prompt, options):
        captured_in_flight_during_call.append(sess._snapshot.in_flight)
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
        yield AssistantMessage(
            content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")],
            model="claude",
        )
        yield ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="test",
            total_cost_usd=0.0, result="x",
        )

    with patch("reverser.tools.dispatch.query", capturing_query):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "test",
            "target": "10.10.10.5",
            "hypothesis_id": 1,
        })

    # During the SDK call, in_flight should have been set
    assert captured_in_flight_during_call[0] is not None
    assert captured_in_flight_during_call[0].specialty == "ad"
    assert captured_in_flight_during_call[0].sub_goal == "test"
    # After dispatch returns, in_flight is cleared
    assert sess._snapshot.in_flight is None


def test_dispatch_in_flight_cleared_on_error(monkeypatch, tmp_path):
    """in_flight is cleared even when dispatch raises."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
    )
    current_session.set(sess)

    async def fail_query(prompt, options):
        raise RuntimeError("boom")
        yield  # pragma: no cover  # makes async generator

    with patch("reverser.tools.dispatch.query", fail_query):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "test",
            "target": "10.10.10.5",
        })

    assert sess._snapshot.in_flight is None
