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
    async def stalls():
        yield 0
        await asyncio.sleep(5)   # longer than the idle window
        yield 1                  # never reached

    agen = stalls()
    with pytest.raises(_DispatchStalled):
        await _collect(_aiter_with_idle_timeout(agen, 0.2))


def test_idle_timeout_reads_env(monkeypatch):
    monkeypatch.delenv("REVERSER_DISPATCH_IDLE_TIMEOUT", raising=False)
    assert _dispatch_idle_timeout() == 300.0
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "12.5")
    assert _dispatch_idle_timeout() == 12.5
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "garbage")
    assert _dispatch_idle_timeout() == 300.0
