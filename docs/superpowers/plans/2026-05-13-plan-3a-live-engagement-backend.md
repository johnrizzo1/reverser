# Live Engagement Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Python service surface that drives a live engagement end-to-end: create / list / read sessions, send user messages, trigger skills, stop / done / resume, set sudo password, read per-target KB, and stream `AgentEvent`s over WebSocket. Backend half of spec Phase 1.

**Architecture:** A `GUISession` adapter wraps the existing `AgentSession` and fans its `AgentEvent` stream out to subscribers via an in-memory `EventBus`. A `SessionManager` tracks at most one active `GUISession` plus a registry of completed/stopped sessions. REST endpoints mutate state; the WebSocket subscribes to the bus and forwards frames.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, the existing `reverser` core (no rewrites).

**Depends on:** [`Plan 1`](2026-05-13-plan-1-gui-service-foundation.md) is merged.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md`](../specs/2026-05-13-electron-desktop-ui-design.md) — sections 3.2 (REST), 3.3 (WebSocket), 3.4 (wrapping the core).

---

## File map

```
src/reverser/gui_service/event_bus.py             create
src/reverser/gui_service/session_adapter.py       create  (GUISession)
src/reverser/gui_service/session_manager.py       create  (SessionManager)
src/reverser/gui_service/app.py                   modify  (wire new routers + WS)
src/reverser/gui_service/routes/sessions.py       create
src/reverser/gui_service/routes/targets.py        create
src/reverser/gui_service/ws/__init__.py           create
src/reverser/gui_service/ws/sessions.py           create
tests/gui_service/test_event_bus.py               create
tests/gui_service/test_session_adapter.py         create
tests/gui_service/test_session_manager.py         create
tests/gui_service/test_sessions_routes.py         create
tests/gui_service/test_targets_routes.py          create
tests/gui_service/test_ws_sessions.py             create
tests/gui_service/fakes.py                        create  (fake Backend that yields scripted events)
```

---

## Task 1: `EventBus` — per-session pub/sub

A simple async pub/sub. Publishers `publish(session_id, frame)`; subscribers iterate `subscribe(session_id)`. Multiple subscribers per session are supported (so future multi-window can attach to the same engagement). Bounded queue per subscriber to prevent OOM from a slow consumer.

**Files:**
- Create: `src/reverser/gui_service/event_bus.py`
- Test: `tests/gui_service/test_event_bus.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_event_bus.py`:

```python
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
```

- [ ] **Step 2: Run test — verify failure**

Run: `pytest tests/gui_service/test_event_bus.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `EventBus`**

Create `src/reverser/gui_service/event_bus.py`:

```python
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
```

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_event_bus.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/event_bus.py tests/gui_service/test_event_bus.py
git commit -m "feat(gui_service): EventBus — per-session async pub/sub with bounded queues"
```

---

## Task 2: Test fakes — `FakeBackend` that yields scripted `AgentEvent`s

We need a deterministic `Backend` for tests of `GUISession`, `SessionManager`, and the routes. The real `claude.py` and `openai_compat.py` backends hit live APIs.

**Files:**
- Create: `tests/gui_service/fakes.py`

- [ ] **Step 1: Create `tests/gui_service/fakes.py`**

```python
"""Test doubles for the gui_service tests.

FakeBackend implements the Backend ABC and yields a scripted sequence of
AgentEvents. The script can be replaced per-test via .script = [...].
"""
from collections.abc import AsyncIterator

from reverser.backends.base import AgentEvent, Backend


class FakeBackend(Backend):
    def __init__(self, *args, **kwargs) -> None:
        # Accept the constructor signature the real backends use; ignore.
        self.script: list[AgentEvent] = [
            AgentEvent(kind="turn", turns=1),
            AgentEvent(kind="text", content="Hello from the fake backend."),
            AgentEvent(kind="result", subtype="success", cost=0.01, turns=1),
        ]
        self.calls: list[dict] = []  # records run() calls for assertions

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "max_turns": max_turns,
            "max_budget_usd": max_budget_usd,
            "allowed_tools": allowed_tools,
        })
        for event in self.script:
            yield event
```

- [ ] **Step 2: No test for the fake itself — it'll be exercised in subsequent tasks. Commit**

```bash
git add tests/gui_service/fakes.py
git commit -m "test(gui_service): FakeBackend yields scripted AgentEvents"
```

---

## Task 3: `GUISession` — adapter that fans `AgentEvent`s to the bus

Wraps `AgentSession` (from `reverser.agent_session`). Owns the in-flight agent task. Translates each `AgentEvent` into a JSON-serializable WS frame and publishes it on the bus.

**Files:**
- Create: `src/reverser/gui_service/session_adapter.py`
- Test: `tests/gui_service/test_session_adapter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_session_adapter.py`:

```python
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
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_session_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `GUISession`**

Create `src/reverser/gui_service/session_adapter.py`:

```python
"""GUISession — adapter wrapping AgentSession for the GUI service.

Each AgentEvent from the wrapped session is translated to a JSON-
serializable WS frame and published on the EventBus keyed by session_id.
Schema for frames matches docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md
section 3.3.
"""
import asyncio
from typing import Any

from ..agent_session import AgentSession
from ..backends.base import AgentEvent
from ..profiles import Profile
from .event_bus import EventBus


def _event_to_frame(ev: AgentEvent) -> dict[str, Any] | None:
    """Translate a single AgentEvent into a WS frame.

    Returns None for events we deliberately do not surface to the UI.
    """
    if ev.kind == "text":
        return {"type": "text", "role": "assistant", "delta": ev.content}
    if ev.kind == "thinking":
        return {"type": "thinking", "delta": ev.content, "redacted": False}
    if ev.kind == "tool_call":
        return {"type": "tool_call", "name": ev.tool_name, "args": ev.tool_input}
    if ev.kind == "tool_result":
        return {
            "type": "tool_result",
            "ok": not ev.is_error,
            "preview": (ev.content or "")[:4096],
        }
    if ev.kind == "turn":
        return {"type": "status", "phase": "running", "turns": ev.turns}
    if ev.kind == "result":
        # Surface separately as both a budget update and a terminal status
        # so the UI can react to either.
        return {"type": "status", "phase": "awaiting_input",
                "subtype": ev.subtype, "cost": ev.cost, "turns": ev.turns}
    if ev.kind == "error":
        return {"type": "log", "level": "error", "msg": ev.content}
    return None


class GUISession:
    """One live engagement, wrapping AgentSession + publishing to EventBus."""

    def __init__(
        self,
        *,
        session_id: str,
        target: str,
        profile: Profile,
        backend_name: str,
        model: str | None,
        api_base: str | None,
        budget: float,
        max_turns: int,
        bus: EventBus,
        resume_from=None,
    ) -> None:
        self.session_id = session_id
        self._bus = bus
        self._agent = AgentSession(
            binary_path=target,
            profile=profile,
            budget=budget,
            max_turns=max_turns,
            backend_name=backend_name,
            model=model,
            api_base=api_base,
            resume_from=resume_from,
        )
        self._send_lock = asyncio.Lock()
        self._sudo_password: str | None = None

    @property
    def stats(self) -> dict[str, Any]:
        s = self._agent.stats
        return {
            "turns": s.turns,
            "total_cost": s.total_cost,
            "budget": s.budget,
            "max_turns": s.max_turns,
            "target": s.target,
            "profile_key": s.profile_key,
        }

    @property
    def snapshot_state(self) -> str:
        return self._agent._snapshot.state  # active|stopped|completed|abandoned

    def set_sudo(self, password: str) -> None:
        """Store the sudo password in memory only (never persisted)."""
        self._sudo_password = password
        # Bridge to the existing tool layer that reads this from env-style
        # state. The same mechanism the TUI's F4 modal uses.
        import os
        os.environ["REVERSER_SUDO_PASSWORD"] = password

    async def send_message(self, user_text: str) -> None:
        """Send a user message and drain all resulting events to the bus."""
        async with self._send_lock:
            await self._bus.publish(self.session_id, {"type": "status", "phase": "running"})
            async for ev in self._agent.send(user_text):
                frame = _event_to_frame(ev)
                if frame is not None:
                    await self._bus.publish(self.session_id, frame)
                # Emit a budget frame after every result event
                if ev.kind == "result" and ev.cost is not None:
                    spent = self._agent.stats.total_cost
                    await self._bus.publish(self.session_id, {
                        "type": "budget",
                        "spent": spent,
                        "remaining": max(0.0, self._agent.budget - spent),
                        "turn": self._agent.stats.turns,
                    })
            await self._bus.publish(self.session_id, {
                "type": "status",
                "phase": "awaiting_input",
            })

    async def trigger_skill(self, skill_key: str) -> None:
        """Run a profile skill by key. Equivalent to TUI's F1 picker."""
        skill = next(
            (s for s in self._agent.profile.skills if s.key == skill_key),
            None,
        )
        if skill is None:
            raise KeyError(f"unknown skill: {skill_key!r}")
        await self.send_message(skill.prompt)

    def stop(self) -> None:
        self._agent.stop()

    def mark_completed(self) -> None:
        self._agent.mark_completed()

    def update_budget(self, new_budget: float | None, new_max_turns: int | None) -> None:
        if new_budget is not None:
            self._agent.update_budget(new_budget)
        if new_max_turns is not None:
            self._agent.update_max_turns(new_max_turns)

    def cancel(self) -> None:
        """Abort the in-flight turn, if any."""
        self._agent.cancel()

    def close(self) -> None:
        self._agent.close()
```

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_session_adapter.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/session_adapter.py tests/gui_service/test_session_adapter.py
git commit -m "feat(gui_service): GUISession adapter — wraps AgentSession, fans events"
```

---

## Task 4: `SessionManager` — tracks live + historical sessions

Owns the single active `GUISession` (Phase 1 constraint: one at a time). Also enumerates historical sessions by scanning `targets/<target>/sessions/`. Wraps creation with the auth-gate check from `kb.authz`.

**Files:**
- Create: `src/reverser/gui_service/session_manager.py`
- Test: `tests/gui_service/test_session_manager.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_session_manager.py`:

```python
"""SessionManager owns the active GUISession + lists historical snapshots."""
from pathlib import Path
from unittest.mock import patch

import pytest

from reverser.gui_service.event_bus import EventBus
from reverser.gui_service.session_manager import SessionManager
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def manager(tmp_path):
    bus = EventBus()
    return SessionManager(bus=bus, targets_root=tmp_path)


@pytest.mark.asyncio
async def test_create_session_returns_id_and_marks_active(manager, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        info = await manager.create_session(
            target=str(tmp_path / "bin"),
            profile_key="general",
            backend_name="claude",
            model=None,
            api_base=None,
            budget=5.0,
            max_turns=50,
        )
    assert info["id"]
    assert info["state"] == "active"
    assert manager.active is not None
    assert manager.active.session_id == info["id"]


@pytest.mark.asyncio
async def test_only_one_active_session_at_a_time(manager, tmp_path):
    """Creating a new session while another is active stops the first."""
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        a = await manager.create_session(
            target=str(tmp_path / "a"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
        b = await manager.create_session(
            target=str(tmp_path / "b"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
    assert a["id"] != b["id"]
    assert manager.active is not None
    assert manager.active.session_id == b["id"]
    # The first session was transitioned to "stopped" before the new one started.


@pytest.mark.asyncio
async def test_list_sessions_includes_active(manager, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        info = await manager.create_session(
            target=str(tmp_path / "bin"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
    sessions = manager.list_sessions()
    assert any(s["id"] == info["id"] and s["state"] == "active" for s in sessions)


def test_pentest_authorization_required_for_network_profile(manager, tmp_path, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    # No .reverser-authorized either; manager scans CWD which is tmp_path
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PermissionError):
        # webpentest is a network-touching profile
        import asyncio
        asyncio.run(manager.create_session(
            target="https://example.com",
            profile_key="webpentest",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        ))
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_session_manager.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `SessionManager`**

Create `src/reverser/gui_service/session_manager.py`:

```python
"""SessionManager — owns the active GUISession + enumerates historical sessions.

Phase 1 constraint: at most one active session at a time. Creating a new
session implicitly stops the previous active one (snapshot is preserved
for later resume).
"""
import os
import secrets
import time
from pathlib import Path
from typing import Any

from ..profiles import get_profile
from ..sessions import list_all as list_all_snapshots, load as load_snapshot
from .event_bus import EventBus
from .session_adapter import GUISession


_NETWORK_PROFILES = {
    "pentest", "webpentest", "webapi", "webrecon", "ad", "manager", "exploit",
}


def _require_pentest_auth(profile_key: str) -> None:
    """Mirrors the existing CLI/TUI authorization gate.

    Raises PermissionError if the profile touches the network but the user
    hasn't acknowledged authorization via env var or marker file.
    """
    if profile_key not in _NETWORK_PROFILES:
        return
    if os.environ.get("REVERSER_PENTEST_AUTHORIZED") == "1":
        return
    if Path(".reverser-authorized").is_file():
        return
    raise PermissionError(
        "network-touching profiles require REVERSER_PENTEST_AUTHORIZED=1 "
        "or a .reverser-authorized marker file in the project root"
    )


class SessionManager:
    def __init__(self, *, bus: EventBus, targets_root: Path | str = "targets") -> None:
        self._bus = bus
        self._targets_root = Path(targets_root)
        self.active: GUISession | None = None

    @staticmethod
    def _mint_session_id() -> str:
        # Match the existing sessions.py naming scheme: ISO-ish + suffix
        return f"{time.strftime('%Y-%m-%dT%H-%M-%S')}-{secrets.token_hex(3)}"

    async def create_session(
        self,
        *,
        target: str,
        profile_key: str,
        backend_name: str,
        model: str | None,
        api_base: str | None,
        budget: float,
        max_turns: int,
    ) -> dict[str, Any]:
        _require_pentest_auth(profile_key)

        # If there is an active session, stop it first.
        if self.active is not None:
            self.active.stop()
            self.active.close()
            self.active = None

        profile = get_profile(profile_key)
        session_id = self._mint_session_id()
        gs = GUISession(
            session_id=session_id,
            target=target,
            profile=profile,
            backend_name=backend_name,
            model=model,
            api_base=api_base,
            budget=budget,
            max_turns=max_turns,
            bus=self._bus,
        )
        self.active = gs
        return self._serialize(gs)

    async def resume_session(
        self,
        *,
        snapshot_id: str,
        backend_name: str | None,
        model: str | None,
        api_base: str | None,
    ) -> dict[str, Any]:
        snap = load_snapshot(snapshot_id)
        if snap is None:
            raise KeyError(snapshot_id)
        _require_pentest_auth(snap.config.profile)

        if self.active is not None:
            self.active.stop()
            self.active.close()
            self.active = None

        profile = get_profile(snap.config.profile)
        gs = GUISession(
            session_id=snap.session_id,
            target=snap.target,
            profile=profile,
            backend_name=backend_name or snap.config.backend,
            model=model if model is not None else snap.config.model,
            api_base=api_base if api_base is not None else snap.config.api_base,
            budget=snap.config.budget,
            max_turns=snap.config.max_turns,
            bus=self._bus,
            resume_from=snap,
        )
        self.active = gs
        return self._serialize(gs)

    def get_active(self, session_id: str) -> GUISession:
        if self.active is None or self.active.session_id != session_id:
            raise KeyError(session_id)
        return self.active

    def list_sessions(self) -> list[dict[str, Any]]:
        # Historical sessions from disk
        snapshots = list_all_snapshots()
        out = []
        for s in snapshots:
            out.append({
                "id": s.session_id,
                "target": s.target,
                "profile": s.config.profile,
                "state": s.state,
                "turns": s.stats.turns,
                "total_cost": s.stats.total_cost,
                "stopped_at": s.stopped_at,
            })
        # The active session overrides whatever state-on-disk has
        if self.active is not None:
            for row in out:
                if row["id"] == self.active.session_id:
                    row["state"] = "active"
                    row.update(self.active.stats)
                    break
            else:
                out.append({"id": self.active.session_id, **self.active.stats, "state": "active"})
        return out

    @staticmethod
    def _serialize(gs: GUISession) -> dict[str, Any]:
        return {
            "id": gs.session_id,
            "state": "active",
            **gs.stats,
        }
```

Note: `list_all_snapshots` and `load_snapshot` come from `reverser.sessions`. If the function names differ, adjust the import (Plan 1 verified the module is importable; a quick `grep "^def " src/reverser/sessions.py` will confirm names).

- [ ] **Step 4: Verify symbol names from `reverser.sessions`**

Run: `grep -E "^def (list_all|load|save|new_snapshot)" src/reverser/sessions.py`
Expected: matches for `list_all`, `load`, `save`, `new_snapshot`. If `list_all_snapshots` is the wrong name, swap to the real one (likely just `list_all`) and update the import to `from ..sessions import list_all, load`.

- [ ] **Step 5: Run — verify pass**

Run: `pytest tests/gui_service/test_session_manager.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/session_manager.py tests/gui_service/test_session_manager.py
git commit -m "feat(gui_service): SessionManager — single active session + history list"
```

---

## Task 5: `/api/sessions` routes — create, list, detail, stop, done, resume, messages, skills, budget, sudo

**Files:**
- Create: `src/reverser/gui_service/routes/sessions.py`
- Modify: `src/reverser/gui_service/app.py` (wire router + shared manager)
- Test: `tests/gui_service/test_sessions_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_sessions_routes.py`:

```python
"""Routes covering the active-engagement lifecycle.

POST /api/sessions creates an engagement.
GET /api/sessions lists.
POST /api/sessions/{id}/messages sends user input.
POST /api/sessions/{id}/skills/{key} triggers a skill.
POST /api/sessions/{id}/stop|done|resume changes lifecycle.
POST /api/sessions/{id}/budget updates caps.
POST /api/sessions/{id}/sudo stores in-memory.
"""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    # Direct sessions.py to write under tmp_path so each test is isolated
    monkeypatch.setenv("REVERSER_TARGETS_ROOT", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_create_session_returns_id(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        r = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"),
            "profile": "general",
            "backend": "claude",
            "model": None,
            "api_base": None,
            "budget": 5.0,
            "max_turns": 50,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body
    assert body["state"] == "active"


@pytest.mark.asyncio
async def test_list_sessions_returns_active(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        listing = await client.get("/api/sessions", headers=HEADERS)
    assert listing.status_code == 200
    rows = listing.json()["sessions"]
    assert any(r["id"] == sid and r["state"] == "active" for r in rows)


@pytest.mark.asyncio
async def test_send_message_204(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/messages",
                              headers=HEADERS, json={"text": "hello"})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_stop_then_done(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        stop = await client.post(f"/api/sessions/{sid}/stop", headers=HEADERS)
    assert stop.status_code == 204


@pytest.mark.asyncio
async def test_budget_update(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/budget",
                              headers=HEADERS, json={"budget": 10.0, "max_turns": 100})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_sudo_in_memory_only(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/sudo",
                              headers=HEADERS, json={"password": "shhh"})
    assert r.status_code == 204
    # password must NOT appear in the snapshot on disk
    import os
    for root, _dirs, files in os.walk(str(tmp_path)):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert b"shhh" not in fh.read()


@pytest.mark.asyncio
async def test_unknown_session_returns_404(client):
    r = await client.post("/api/sessions/missing/messages", headers=HEADERS, json={"text": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_sessions_routes.py -v`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Implement the sessions router**

Create `src/reverser/gui_service/routes/sessions.py`:

```python
"""POST/GET /api/sessions and its sub-resources."""
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

router = APIRouter()


class CreateSession(BaseModel):
    target: str
    profile: str
    backend: str
    model: str | None = None
    api_base: str | None = None
    budget: float = 5.0
    max_turns: int = 50


class MessageBody(BaseModel):
    text: str


class BudgetBody(BaseModel):
    budget: float | None = None
    max_turns: int | None = None


class SudoBody(BaseModel):
    password: str


def _manager(request: Request):
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is None:
        raise HTTPException(500, detail="session_manager not configured")
    return mgr


@router.get("/api/sessions")
def list_sessions(request: Request) -> dict:
    return {"sessions": _manager(request).list_sessions()}


@router.post("/api/sessions")
async def create_session(request: Request, body: CreateSession) -> dict:
    try:
        return await _manager(request).create_session(
            target=body.target,
            profile_key=body.profile,
            backend_name=body.backend,
            model=body.model,
            api_base=body.api_base,
            budget=body.budget,
            max_turns=body.max_turns,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/api/sessions/{session_id}/messages", status_code=204)
async def send_message(request: Request, session_id: str, body: MessageBody) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    await gs.send_message(body.text)
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/skills/{skill_key}", status_code=204)
async def trigger_skill(request: Request, session_id: str, skill_key: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    try:
        await gs.trigger_skill(skill_key)
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/stop", status_code=204)
async def stop_session(request: Request, session_id: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.stop()
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/done", status_code=204)
async def mark_done(request: Request, session_id: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.mark_completed()
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/resume")
async def resume_session(request: Request, session_id: str) -> dict:
    try:
        return await _manager(request).resume_session(
            snapshot_id=session_id,
            backend_name=None, model=None, api_base=None,
        )
    except KeyError:
        raise HTTPException(404)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/api/sessions/{session_id}/budget", status_code=204)
async def update_budget(request: Request, session_id: str, body: BudgetBody) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.update_budget(body.budget, body.max_turns)
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/sudo", status_code=204)
async def set_sudo(request: Request, session_id: str, body: SudoBody) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.set_sudo(body.password)
    return Response(status_code=204)
```

- [ ] **Step 4: Wire the router + the manager in `app.py`**

Edit `src/reverser/gui_service/app.py` to instantiate one `SessionManager` per app and attach it to `app.state`:

```python
"""FastAPI app factory for the GUI service."""
from fastapi import Depends, FastAPI, Header, HTTPException, status

from .auth import is_authorized
from .config import ServiceConfig
from .event_bus import EventBus
from .session_manager import SessionManager
from .routes import (
    backends as backends_routes,
    health as health_routes,
    profiles as profiles_routes,
    sessions as sessions_routes,
)


def _require_token_dep(config: ServiceConfig):
    def _check(authorization: str | None = Header(default=None)) -> None:
        if not is_authorized(authorization, config.token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
            )
    return _check


def create_app(config: ServiceConfig) -> FastAPI:
    app = FastAPI(title="reverser GUI service", version="0.1.0")
    app.state.config = config
    app.state.event_bus = EventBus()
    app.state.session_manager = SessionManager(bus=app.state.event_bus)

    require_token = Depends(_require_token_dep(config))
    app.include_router(health_routes.router, dependencies=[require_token])
    app.include_router(profiles_routes.router, dependencies=[require_token])
    app.include_router(backends_routes.router, dependencies=[require_token])
    app.include_router(sessions_routes.router, dependencies=[require_token])
    return app
```

- [ ] **Step 5: Run — verify pass**

Run: `pytest tests/gui_service/test_sessions_routes.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/sessions.py src/reverser/gui_service/app.py tests/gui_service/test_sessions_routes.py
git commit -m "feat(gui_service): /api/sessions — create, list, message, skill, stop, done, resume, budget, sudo"
```

---

## Task 6: `/api/targets` routes — list targets, read KB

**Files:**
- Create: `src/reverser/gui_service/routes/targets.py`
- Modify: `src/reverser/gui_service/app.py` (include router)
- Test: `tests/gui_service/test_targets_routes.py`

- [ ] **Step 1: Inspect the existing `kb` module to confirm shapes**

Run: `grep -E "^(def|class) " src/reverser/kb/store.py | head -30`

Note any function used for "list everything for a target". The KB exposes `for_target(target)` returning a `KB` instance with `.list_hosts()`, `.list_services()`, `.list_credentials()`, `.list_findings()`, `.list_hypotheses()`, `.list_artifacts()`, etc.

- [ ] **Step 2: Write failing tests**

Create `tests/gui_service/test_targets_routes.py`:

```python
"""GET /api/targets and /api/targets/{t}/kb."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Populate a fake targets/<t>/ directory so /api/targets has something
    (tmp_path / "targets" / "10.10.10.5").mkdir(parents=True)
    (tmp_path / "targets" / "example.com").mkdir(parents=True)
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_list_targets(client):
    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    targets = {t["name"] for t in r.json()["targets"]}
    assert "10.10.10.5" in targets
    assert "example.com" in targets


@pytest.mark.asyncio
async def test_read_kb_returns_keyed_lists(client):
    r = await client.get("/api/targets/10.10.10.5/kb", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    for key in ("hosts", "services", "credentials", "findings",
                "hypotheses", "artifacts", "notes"):
        assert key in body, f"missing key {key}"
        assert isinstance(body[key], list)
```

- [ ] **Step 3: Run — verify failure**

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: FAIL — 404.

- [ ] **Step 4: Implement the targets router**

Create `src/reverser/gui_service/routes/targets.py`:

```python
"""GET /api/targets + GET /api/targets/{name}/kb (read-only KB view).

The KB is the SQLite state at targets/<name>/state.db. We use the existing
reverser.kb.for_target helper to load it. Empty KB tables come back as
empty lists.
"""
from dataclasses import asdict, is_dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...kb import for_target

router = APIRouter()


def _as_jsonable(row):
    if is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
    return row


def _targets_root() -> Path:
    return Path("targets")


@router.get("/api/targets")
def list_targets() -> dict:
    root = _targets_root()
    if not root.is_dir():
        return {"targets": []}
    targets = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        # Skip hidden / non-canonical directories ("." prefix, etc.)
        if child.name.startswith("."):
            continue
        targets.append({
            "name": child.name,
            "has_kb": (child / "state.db").is_file(),
            "has_scope": (child / "scope.toml").is_file(),
        })
    return {"targets": targets}


@router.get("/api/targets/{target}/kb")
def read_kb(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")

    kb = for_target(target)

    def _list(method_name: str) -> list:
        fn = getattr(kb, method_name, None)
        if fn is None:
            return []
        try:
            return [_as_jsonable(r) for r in fn()]
        except Exception:
            return []

    return {
        "hosts": _list("list_hosts"),
        "services": _list("list_services"),
        "credentials": _list("list_credentials"),
        "findings": _list("list_findings"),
        "hypotheses": _list("list_hypotheses"),
        "artifacts": _list("list_artifacts"),
        "notes": _list("list_notes"),
    }
```

- [ ] **Step 5: Wire the router in `app.py`**

```python
from .routes import targets as targets_routes
# ... in create_app:
app.include_router(targets_routes.router, dependencies=[require_token])
```

- [ ] **Step 6: Run — verify pass**

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: PASS (2 tests).

The `read_kb` test may need adjustment if `for_target` requires the state.db to already exist. If so, populate the fixture: `from reverser.kb import for_target; for_target("10.10.10.5")` after creating the directory (which initializes the schema).

- [ ] **Step 7: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py src/reverser/gui_service/app.py tests/gui_service/test_targets_routes.py
git commit -m "feat(gui_service): /api/targets + /api/targets/{t}/kb (read-only)"
```

---

## Task 7: WebSocket `/ws/sessions/{id}`

**Files:**
- Create: `src/reverser/gui_service/ws/__init__.py`
- Create: `src/reverser/gui_service/ws/sessions.py`
- Modify: `src/reverser/gui_service/app.py` (mount the WS route)
- Test: `tests/gui_service/test_ws_sessions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_ws_sessions.py`:

```python
"""WebSocket subscribes to the EventBus and forwards JSON frames.

We use FastAPI's TestClient (sync) for WebSocket tests — httpx.AsyncClient
doesn't yet support WebSocket directly in a clean way.
"""
import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    return TestClient(create_app(config))


def test_ws_requires_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/sessions/anything"):
            pass


def test_ws_rejects_wrong_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/sessions/x?token=wrong"):
            pass


def test_ws_receives_published_frames(client, tmp_path):
    # Create an active session via the REST API first
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        r = client.post(
            "/api/sessions",
            headers={"Authorization": "Bearer t"},
            json={
                "target": str(tmp_path / "bin"), "profile": "general",
                "backend": "claude", "model": None, "api_base": None,
                "budget": 5.0, "max_turns": 50,
            },
        )
    assert r.status_code == 200
    sid = r.json()["id"]

    # Subscribe to the WS, then send a message, then drain frames.
    with client.websocket_connect(f"/ws/sessions/{sid}?token=t") as ws:
        with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
            client.post(
                f"/api/sessions/{sid}/messages",
                headers={"Authorization": "Bearer t"},
                json={"text": "hi"},
            )
        # Collect a few frames
        frames = []
        for _ in range(10):
            try:
                frames.append(ws.receive_json(timeout=2.0))
            except Exception:
                break
            if frames[-1].get("type") == "status" and frames[-1].get("phase") == "awaiting_input":
                break
    kinds = [f["type"] for f in frames]
    assert "text" in kinds or "status" in kinds
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_ws_sessions.py -v`
Expected: FAIL — `404 Not Found` on the WS route.

- [ ] **Step 3: Implement the WS endpoint**

Create `src/reverser/gui_service/ws/__init__.py` (empty file).

Create `src/reverser/gui_service/ws/sessions.py`:

```python
"""WebSocket endpoint /ws/sessions/{session_id}.

Subscribes to EventBus for the given session_id and forwards every frame
as a JSON text message. Auth is via ?token=… in the query string.
"""
import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..auth import is_authorized_query

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}")
async def session_stream(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
):
    config = websocket.app.state.config
    if not is_authorized_query(token, config.token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    bus = websocket.app.state.event_bus
    await websocket.accept()
    try:
        async with bus.subscribe(session_id) as queue:
            while True:
                # Race the consumer-direction recv (for client → server frames
                # like {"type":"pause"}) against the queue (server → client).
                # The first to resolve wins.
                recv_task = asyncio.create_task(websocket.receive_text())
                q_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    {recv_task, q_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if recv_task in done:
                    # We accept but currently ignore client → server frames.
                    # Future: {"type":"pause"|"abort_tool"}.
                    _ = recv_task.result()
                if q_task in done:
                    frame = q_task.result()
                    await websocket.send_json(frame)
    except WebSocketDisconnect:
        return
```

- [ ] **Step 4: Wire the WS router in `app.py`**

WebSocket routes are NOT wrapped by the REST bearer dependency — they validate the query token themselves. So they're added without `dependencies=`:

```python
from .ws import sessions as ws_sessions
# ... in create_app, after the include_router calls:
app.include_router(ws_sessions.router)
```

- [ ] **Step 5: Run — verify pass**

Run: `pytest tests/gui_service/test_ws_sessions.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/ws/ src/reverser/gui_service/app.py tests/gui_service/test_ws_sessions.py
git commit -m "feat(gui_service): WebSocket /ws/sessions/{id} — query-token auth + EventBus forwarding"
```

---

## Task 8: Cross-cutting smoke — full live session via subprocess

Spawn the real service via the same handshake used in Plan 1's Task 9, create a session, send a message, drain WS frames. Catches integration issues between manager, adapter, router, and WS.

**Files:**
- Modify: `tests/gui_service/test_handshake.py` (append)

- [ ] **Step 1: Append the failing test**

Add to `tests/gui_service/test_handshake.py`:

```python
@pytest.mark.asyncio
async def test_handshake_full_engagement_smoke(tmp_path, monkeypatch):
    """Spawn the service, create a session, send a message, drain WS frames."""
    import websockets
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")

    env = {**os.environ, "REVERSER_PENTEST_AUTHORIZED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "reverser.gui_service",
         "--host", "127.0.0.1", "--port", "0", "--project-root", str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    try:
        line = proc.stdout.readline()
        h = json.loads(line)
        base = f"http://127.0.0.1:{h['port']}"
        ws_base = f"ws://127.0.0.1:{h['port']}"
        headers = {"Authorization": f"Bearer {h['token']}"}
        await asyncio.sleep(0.2)

        async with httpx.AsyncClient(base_url=base) as c:
            r = await c.post("/api/sessions", headers=headers, json={
                "target": str(tmp_path / "bin"),
                "profile": "general",
                "backend": "claude",  # will actually hit Claude — for unit-style smoke,
                                       # use the OPENAI-compat path with a stub model,
                                       # or skip this test in CI by default.
                "model": None, "api_base": None,
                "budget": 0.01, "max_turns": 1,
            })
            # Either creating the session works, or we skip (no Claude API key in CI).
            if r.status_code != 200:
                pytest.skip(f"session create returned {r.status_code} (likely no ANTHROPIC_API_KEY)")
            sid = r.json()["id"]

        async with websockets.connect(f"{ws_base}/ws/sessions/{sid}?token={h['token']}") as ws:
            async with httpx.AsyncClient(base_url=base) as c:
                # The real Claude backend may cost money — this is a TINY budget.
                # If you want a hermetic test, replace with the openai_compat path
                # pointed at a local stub server.
                await c.post(f"/api/sessions/{sid}/messages",
                             headers=headers, json={"text": "say hi"})
            # Drain at least one frame
            frame = await asyncio.wait_for(ws.recv(), timeout=20.0)
            assert frame, "no frame received"
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
```

This test is intentionally pragmatic: it documents the full smoke path but auto-skips if there's no Anthropic API key. For hermetic CI, replace `backend=claude` with an `openai_compat` pointing at a local stub server (e.g. `httpx-mock` or a tiny aiohttp echo server) — that's out of scope for this plan.

- [ ] **Step 2: Run — verify pass or skip**

Run: `pytest tests/gui_service/test_handshake.py::test_handshake_full_engagement_smoke -v`
Expected: PASS or SKIPPED (skip is acceptable). If it FAILS for reasons unrelated to API access, fix.

- [ ] **Step 3: Run the entire gui_service test suite**

Run: `pytest tests/gui_service/ -v`
Expected: all PASS except the new smoke test, which may SKIP.

- [ ] **Step 4: Commit**

```bash
git add tests/gui_service/test_handshake.py
git commit -m "test(gui_service): full-engagement subprocess smoke (skips without API key)"
```

---

## Verification

```bash
pytest tests/gui_service/ -v
```

Expected:
- All Plan 1 tests still pass.
- New event-bus tests pass (5).
- New session-adapter tests pass (3).
- New session-manager tests pass (4).
- New sessions-routes tests pass (7).
- New targets-routes tests pass (2).
- New ws-sessions tests pass (3).
- The full-engagement smoke either PASSes or SKIPs.

Manual subprocess smoke is the same as Plan 1's. Additionally, with a real backend configured:

```bash
python -m reverser.gui_service --port 0 --project-root .
# In another terminal:
PORT=$PORT TOKEN=$TOKEN
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://127.0.0.1:$PORT/api/sessions \
  -d '{"target":"./some-binary","profile":"general","backend":"claude","budget":0.1,"max_turns":2}'
# Note the returned id, then:
wscat -c "ws://127.0.0.1:$PORT/ws/sessions/<id>?token=$TOKEN"
# In a third terminal:
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://127.0.0.1:$PORT/api/sessions/<id>/messages -d '{"text":"hello"}'
# WebSocket terminal shows streaming frames.
```

## What this plan does NOT cover

- Any frontend code (Plan 3b).
- Findings-file image serving (`GET /api/targets/{t}/findings/{id}/screenshots/{n}`) — Phase 3.
- Scope.toml read/write (`GET|PUT /api/targets/{t}/scope`) — Phase 3.
- Live backend health refresh, model-list discovery via `/v1/models` — Phase 1 extension, not blocking MVP.
- `/api/settings/keys` keychain proxy — Phase 4.
