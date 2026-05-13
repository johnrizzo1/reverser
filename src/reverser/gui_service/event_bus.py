"""In-memory async pub/sub keyed by session_id.

Each subscriber gets its own bounded queue. A slow subscriber drops the
*oldest* frame rather than blocking the publisher — this preserves agent
liveness at the cost of UI history fidelity. The frontend never relies on
seeing every frame for state correctness (state changes are also reflected
in REST endpoints).
"""
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


_QUEUE_MAX = 4096  # ~10s of dense streaming at typical agent rates


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subscribers.get(session_id, []))

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subscribers[session_id].append(q)
        try:
            yield q
        finally:
            lst = self._subscribers.get(session_id, [])
            try:
                lst.remove(q)
            except ValueError:
                pass
            if not lst:
                self._subscribers.pop(session_id, None)

    async def publish(self, session_id: str, frame: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(session_id, [])):
            if q.full():
                # Drop oldest so the slow subscriber catches up to recent events
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await q.put(frame)
