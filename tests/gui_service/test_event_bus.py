"""EventBus delivers frames to all subscribers; bounded per-subscriber queue."""
import asyncio
import pytest

from reverser.gui_service.event_bus import EventBus


@pytest.mark.asyncio
async def test_subscriber_receives_published_frames():
    bus = EventBus()
    async with bus.subscribe("s1") as queue:
        await bus.publish("s1", {"type": "text", "delta": "hi"})
        frame = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert frame == {"type": "text", "delta": "hi"}


@pytest.mark.asyncio
async def test_multiple_subscribers_each_get_a_copy():
    bus = EventBus()
    async with bus.subscribe("s1") as a, bus.subscribe("s1") as b:
        await bus.publish("s1", {"type": "ping"})
        assert (await asyncio.wait_for(a.get(), 1.0)) == {"type": "ping"}
        assert (await asyncio.wait_for(b.get(), 1.0)) == {"type": "ping"}


@pytest.mark.asyncio
async def test_session_scoped_no_cross_talk():
    bus = EventBus()
    async with bus.subscribe("s1") as a, bus.subscribe("s2") as b:
        await bus.publish("s1", {"type": "for_s1"})
        await bus.publish("s2", {"type": "for_s2"})
        assert (await asyncio.wait_for(a.get(), 1.0))["type"] == "for_s1"
        assert (await asyncio.wait_for(b.get(), 1.0))["type"] == "for_s2"


@pytest.mark.asyncio
async def test_subscriber_unregister_on_context_exit():
    bus = EventBus()
    async with bus.subscribe("s1"):
        assert bus.subscriber_count("s1") == 1
    assert bus.subscriber_count("s1") == 0


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    await bus.publish("nobody", {"type": "lost"})  # must not raise
