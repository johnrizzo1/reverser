"""Cumulative turn-number remapping across send_message calls.

Backends use a per-`run()` local turn counter that restarts at 1 on every
`AgentSession.send()`. `agent_session` translates the `turn` event itself
to the cumulative count via `self.stats.turns`, but the renderer also
buckets `text` / `tool_call` / `tool_result` / `thinking` events by
`frame.turn`. If those events are yielded with the backend-local turn,
the second user message's events collide with the first message's Turn 1
and Turn 2 buckets in the renderer — text written for the latest turn
disappears under old turn blocks.

These tests pin: after a second send_message call, every yielded
event carries the cumulative turn number, matching the `turns` field on
the yielded "turn" event.
"""
from unittest.mock import patch

import pytest

from reverser.backends.base import AgentEvent
from tests.gui_service.fakes import FakeBackend


def _make_session(tmp_path, monkeypatch, fake_backend):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.agent_session import AgentSession

    with patch("reverser.agent_session.create_backend", return_value=fake_backend):
        return AgentSession(
            binary_path=str(tmp_path / "bin"),
            profile=get_profile("general"),
        )


@pytest.mark.asyncio
async def test_second_send_text_events_carry_cumulative_turn(tmp_path, monkeypatch):
    """The second send_message's text events must NOT carry turn=1 (the
    backend's local counter). They must carry the cumulative turn number
    matching the yielded `turn` event's `turns` field — otherwise the
    renderer drops the prose into the old Turn 1 bucket and the user
    sees no LLM response for the latest message.
    """
    fb = FakeBackend()
    sess = _make_session(tmp_path, monkeypatch, fb)

    # First send_message: 2 backend turns (local 1, 2) → cumulative 1, 2.
    fb.script = [
        AgentEvent(kind="turn", turns=1, turn=1),
        AgentEvent(kind="text", content="first reply turn 1", turn=1),
        AgentEvent(kind="turn", turns=2, turn=2),
        AgentEvent(kind="text", content="first reply turn 2", turn=2),
        AgentEvent(kind="result", subtype="success", cost=0.01, turns=2),
    ]
    first_events = [ev async for ev in sess.send("scan")]
    assert sess.stats.turns == 2

    # Second send_message: backend AGAIN counts local 1, 2 — but the
    # cumulative should be 3, 4.
    fb.script = [
        AgentEvent(kind="turn", turns=1, turn=1),
        AgentEvent(kind="text", content="second reply turn 3", turn=1),
        AgentEvent(kind="tool_call", tool_name="bash",
                   tool_input='{"cmd":"ls"}', tool_use_id="t1", turn=1),
        AgentEvent(kind="tool_result", content="ok",
                   tool_use_id="t1", is_error=False, turn=1),
        AgentEvent(kind="turn", turns=2, turn=2),
        AgentEvent(kind="text", content="second reply turn 4", turn=2),
        AgentEvent(kind="result", subtype="success", cost=0.01, turns=2),
    ]
    second_events = [ev async for ev in sess.send("more")]
    assert sess.stats.turns == 4

    # Index the yielded events by kind for assertions.
    turn_events = [e for e in second_events if e.kind == "turn"]
    text_events = [e for e in second_events if e.kind == "text"]
    tool_events = [e for e in second_events
                   if e.kind in ("tool_call", "tool_result")]

    # The yielded turn events carry the cumulative count
    assert [e.turns for e in turn_events] == [3, 4], turn_events

    # The yielded text events must carry the cumulative turn, not the
    # backend-local 1/2. This is the bug fix: WITHOUT remapping, both
    # texts would have turn=1 and turn=2 and collide with the first
    # send's Turn 1/2 buckets in the renderer.
    assert [e.turn for e in text_events] == [3, 4], (
        f"text events leaked backend-local turn numbers: {[e.turn for e in text_events]}"
    )
    assert "second reply turn 3" in text_events[0].content
    assert "second reply turn 4" in text_events[1].content

    # Tool events from the second send must also carry the cumulative
    # turn number (3, not 1).
    for e in tool_events:
        assert e.turn == 3, (
            f"tool event leaked backend-local turn: {e.kind} turn={e.turn}"
        )


@pytest.mark.asyncio
async def test_single_send_turn_numbers_unchanged(tmp_path, monkeypatch):
    """First send should still produce 1, 2, 3, ... — no off-by-one."""
    fb = FakeBackend()
    fb.script = [
        AgentEvent(kind="turn", turns=1, turn=1),
        AgentEvent(kind="text", content="a", turn=1),
        AgentEvent(kind="turn", turns=2, turn=2),
        AgentEvent(kind="text", content="b", turn=2),
        AgentEvent(kind="result", subtype="success", cost=0.0, turns=2),
    ]
    sess = _make_session(tmp_path, monkeypatch, fb)
    events = [ev async for ev in sess.send("hi")]

    text_events = [e for e in events if e.kind == "text"]
    assert [e.turn for e in text_events] == [1, 2]
