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
