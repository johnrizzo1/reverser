"""GUISession fans AgentEvents from a wrapped AgentSession out to the EventBus
as JSON-serializable WS frames."""
import asyncio
from unittest.mock import patch

import pytest

from reverser.gui_service.event_bus import EventBus
from reverser.gui_service.session_adapter import GUISession
from reverser.profiles import get_profile
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def gui_session(bus, fake_backend, tmp_path):
    profile = get_profile("general")
    # Patch the backend factory used by AgentSession to return our fake.
    with patch("reverser.agent_session.create_backend", return_value=fake_backend):
        gs = GUISession(
            session_id="test-session-1",
            target=str(tmp_path / "binary"),  # path doesn't need to exist for fake
            profile=profile,
            backend_name="claude",
            model=None,
            api_base=None,
            budget=5.0,
            max_turns=50,
            bus=bus,
        )
    yield gs
    gs.close()


@pytest.mark.asyncio
async def test_send_message_publishes_frames(bus, gui_session):
    frames: list[dict] = []
    async with bus.subscribe(gui_session.session_id) as q:
        # Send a message and drain frames concurrently
        send_task = asyncio.create_task(gui_session.send_message("inspect main"))
        # Collect frames until the result frame arrives
        while True:
            frame = await asyncio.wait_for(q.get(), timeout=2.0)
            frames.append(frame)
            if frame.get("type") == "status" and frame.get("phase") == "awaiting_input":
                break
        await send_task

    kinds = [f["type"] for f in frames]
    # The fake yields: turn, text, result. The adapter also emits status frames.
    assert "text" in kinds
    assert "status" in kinds


@pytest.mark.asyncio
async def test_text_frame_carries_delta(bus, gui_session):
    async with bus.subscribe(gui_session.session_id) as q:
        await gui_session.send_message("hi")
        # Drain
        text_frames = []
        for _ in range(20):
            try:
                f = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                break
            if f["type"] == "text":
                text_frames.append(f)
        assert any(f.get("delta") == "Hello from the fake backend." for f in text_frames)


@pytest.mark.asyncio
async def test_budget_frame_tracks_spend(bus, gui_session):
    async with bus.subscribe(gui_session.session_id) as q:
        await gui_session.send_message("hi")
        budget_frames = []
        for _ in range(20):
            try:
                f = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                break
            if f["type"] == "budget":
                budget_frames.append(f)
        # The fake reports cost=0.01 in the result event
        assert any(abs(f.get("spent", 0) - 0.01) < 1e-9 for f in budget_frames)


@pytest.mark.asyncio
async def test_cancel_aborts_in_flight_send(bus, tmp_path):
    """Regression: GUISession.cancel() must actually preempt an in-flight
    send_message that is blocked inside a long-running tool call.

    Before the fix, cancel() only flipped a cooperative flag that
    AgentSession.send() checks BETWEEN events. If the backend was
    awaiting a slow tool, the flag had no effect until the tool returned.
    """
    from reverser.backends.base import AgentEvent, Backend
    from collections.abc import AsyncIterator

    class SlowBackend(Backend):
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, prompt, system_prompt, *, max_turns=50,
                      max_budget_usd=5.0, allowed_tools=None) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(kind="turn", turns=1)
            yield AgentEvent(kind="tool_call", tool_name="bash", tool_input={"cmd": "sleep 30"})
            # Simulate a slow tool — this is where the agent would be stuck
            # waiting for nmap / bash / etc. to return.
            await asyncio.sleep(30.0)
            yield AgentEvent(kind="tool_result", content="done", is_error=False)
            yield AgentEvent(kind="result", subtype="success", cost=0.01, turns=1)

    profile = get_profile("general")
    with patch("reverser.agent_session.create_backend", return_value=SlowBackend()):
        gs = GUISession(
            session_id="cancel-test",
            target=str(tmp_path / "bin"),
            profile=profile,
            backend_name="claude",
            model=None,
            api_base=None,
            budget=5.0,
            max_turns=50,
            bus=bus,
        )
    try:
        send_task = asyncio.create_task(gs.send_message("hi"))
        # Give the task a moment to enter the slow await
        for _ in range(20):
            await asyncio.sleep(0.01)
            if not send_task.done() and gs._current_send_task is not None:
                break

        gs.cancel()

        # Without the fix, this hangs for ~30s and times out. With the fix,
        # the task finishes (with CancelledError or normally) almost immediately.
        try:
            await asyncio.wait_for(send_task, timeout=2.0)
        except asyncio.CancelledError:
            pass  # expected — cancellation propagated
        except asyncio.TimeoutError:
            pytest.fail(
                "send_message did not unwind after cancel() — the in-flight "
                "tool sleep is still blocking. cancel() must cancel the "
                "send task, not just flip a cooperative flag."
            )
    finally:
        gs.close()


@pytest.mark.asyncio
async def test_dispatch_events_publish_to_bus(bus, gui_session):
    """Dispatch sub-agent events (thinking, tool_call, tool_result, text)
    must reach the WebSocket so the renderer can show what each dispatched
    specialist is actually doing. Without this hook, only the parent's
    `dispatch_specialist` tool_call shows up — the user sees the dispatch
    happened but nothing about what the specialist did.
    """
    frames: list[dict] = []
    async with bus.subscribe(gui_session.session_id) as q:
        # Simulate the dispatch tool firing events via the AgentSession hook.
        gui_session._agent.emit_dispatch_event("ad", "abc123", 1, "thinking", "scanning hosts...")
        gui_session._agent.emit_dispatch_event("ad", "abc123", 1, "tool_call", "nmap -sV 10.0.0.0/24")
        gui_session._agent.emit_dispatch_event("ad", "abc123", 1, "tool_result", "open ports: 22, 80")

        for _ in range(3):
            frames.append(await asyncio.wait_for(q.get(), timeout=1.0))

    dispatch_frames = [f for f in frames if f.get("type") == "dispatch"]
    assert len(dispatch_frames) == 3, f"expected 3 dispatch frames, got: {frames}"
    assert dispatch_frames[0]["specialty"] == "ad"
    assert dispatch_frames[0]["dispatch_id"] == "abc123"
    assert dispatch_frames[0]["phase"] == "thinking"
    assert dispatch_frames[0]["content"] == "scanning hosts..."
    assert dispatch_frames[0]["sub_turn"] == 1
    assert "turn" in dispatch_frames[0]
    assert dispatch_frames[1]["phase"] == "tool_call"
    assert dispatch_frames[1]["content"] == "nmap -sV 10.0.0.0/24"
    assert dispatch_frames[2]["phase"] == "tool_result"
    assert dispatch_frames[2]["content"] == "open ports: 22, 80"


@pytest.mark.asyncio
async def test_dispatch_events_attach_to_at_least_first_turn(bus, gui_session):
    """Dispatch events can fire while the parent agent is still early in the
    turn. They must still attach to a visible chat turn, not hidden turn 0.
    """
    frames: list[dict] = []
    gui_session._agent.stats.turns = 0
    async with bus.subscribe(gui_session.session_id) as q:
        gui_session._agent.emit_dispatch_event("ad", "abc123", 0, "start", "{}")
        frames.append(await asyncio.wait_for(q.get(), timeout=1.0))

    assert frames[0]["type"] == "dispatch"
    assert frames[0]["turn"] == 1


@pytest.mark.asyncio
async def test_send_message_sets_current_session_for_kb_events(bus, tmp_path, monkeypatch):
    """GUI sends run in request tasks after session construction.

    KB tools use current_session to emit live hypothesis/finding frames, so
    GUISession.send_message must bind the wrapped AgentSession for the duration
    of the turn.
    """
    from collections.abc import AsyncIterator

    from reverser.backends.base import AgentEvent, Backend
    from reverser.sessions import current_session

    class KbWritingBackend(Backend):
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, prompt, system_prompt, *, max_turns=50,
                      max_budget_usd=5.0, allowed_tools=None) -> AsyncIterator[AgentEvent]:
            from reverser.tools.kb import kb_add_hypothesis

            yield AgentEvent(kind="turn", turns=1)
            fn = getattr(kb_add_hypothesis, "handler", None) or kb_add_hypothesis
            await fn({
                "target": "10.10.10.5",
                "statement": "SMB signing may be disabled",
            })
            yield AgentEvent(kind="result", subtype="success", cost=0.01, turns=1)

    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setattr("reverser.tools.kb._check_auth", lambda: None)
    profile = get_profile("general")
    with patch("reverser.agent_session.create_backend", return_value=KbWritingBackend()):
        gs = GUISession(
            session_id="kb-event-test",
            target="10.10.10.5",
            profile=profile,
            backend_name="claude",
            model=None,
            api_base=None,
            budget=5.0,
            max_turns=50,
            bus=bus,
        )
    try:
        token = current_session.set(None)
        frames: list[dict] = []
        try:
            async with bus.subscribe(gs.session_id) as q:
                await gs.send_message("create hypothesis")
                for _ in range(10):
                    try:
                        frames.append(await asyncio.wait_for(q.get(), timeout=0.5))
                    except asyncio.TimeoutError:
                        break
        finally:
            current_session.reset(token)
    finally:
        gs.close()

    hypothesis_frames = [f for f in frames if f.get("type") == "hypothesis"]
    assert hypothesis_frames, f"expected live hypothesis frame, got: {frames}"
    assert hypothesis_frames[0]["action"] == "create"
    assert hypothesis_frames[0]["row"]["statement"] == "SMB signing may be disabled"


def test_text_frame_has_turn():
    from reverser.backends.base import AgentEvent
    from reverser.gui_service.session_adapter import _event_to_frame
    ev = AgentEvent(kind="text", content="hi", turn=3)
    frame = _event_to_frame(ev)
    assert frame == {"type": "text", "role": "assistant", "delta": "hi", "turn": 3}


def test_tool_call_frame_has_tool_use_id_and_turn():
    from reverser.backends.base import AgentEvent
    from reverser.gui_service.session_adapter import _event_to_frame
    ev = AgentEvent(
        kind="tool_call", tool_name="bash", tool_input="ls",
        tool_use_id="tool_abc", turn=2,
    )
    frame = _event_to_frame(ev)
    assert frame["type"] == "tool_call"
    assert frame["tool_use_id"] == "tool_abc"
    assert frame["turn"] == 2


def test_tool_result_frame_has_tool_use_id_and_turn():
    from reverser.backends.base import AgentEvent
    from reverser.gui_service.session_adapter import _event_to_frame
    ev = AgentEvent(
        kind="tool_result", content="ok", is_error=False,
        tool_use_id="tool_abc", turn=2,
    )
    frame = _event_to_frame(ev)
    assert frame["type"] == "tool_result"
    assert frame["tool_use_id"] == "tool_abc"
    assert frame["turn"] == 2
    assert frame["ok"] is True
