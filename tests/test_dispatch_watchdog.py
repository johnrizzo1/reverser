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
