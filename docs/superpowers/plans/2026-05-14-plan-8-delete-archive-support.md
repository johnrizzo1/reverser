# Delete & Archive Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add archive (reversible hide) and delete (remove from disk) for both sessions and targets, with active-session guards and lazy trash pruning for targets.

**Architecture:** Backend adds 6 new endpoints + 2 shape extensions to existing list endpoints. Sessions archive via an `archived_at` field on the snapshot; targets archive via an `.archived` marker file. Sessions hard-delete; targets soft-delete to `targets/.trash/<ISO-timestamp>-<name>/` and auto-prune after 30 days. Frontend adds two generic confirm modals, hover row actions, a new "archived" Sessions filter tab, and a "Show archived" Targets toggle. All destructive endpoints refuse with 409 when any relevant session has `state == "active"`.

**Tech Stack:** Python 3.11+, FastAPI, pytest+httpx; React 18 + TypeScript, TanStack Query, Tailwind, Playwright.

**Reference spec:** [docs/superpowers/specs/2026-05-14-delete-archive-support-design.md](../specs/2026-05-14-delete-archive-support-design.md)

---

## File Map

**Backend (modify):**
- `src/reverser/sessions.py` — add `archived_at` field; add `set_archived()`, `delete()` helpers
- `src/reverser/gui_service/session_manager.py` — propagate `archived_at` into the SessionRow shape
- `src/reverser/gui_service/routes/sessions.py` — three new endpoints (archive, unarchive, hard delete)
- `src/reverser/gui_service/routes/targets.py` — three new endpoints (archive, unarchive, soft delete) + trash prune + `archived` field

**Backend (create):**
- `tests/test_session_archive_delete.py` — sessions module helpers
- `tests/gui_service/test_session_archive_routes.py` — session routes
- `tests/gui_service/test_target_archive_routes.py` — target routes

**Frontend (modify):**
- `desktop/renderer/src/api/client.ts` — type additions
- `desktop/renderer/src/api/queries.ts` — six new mutation hooks
- `desktop/renderer/src/components/SessionRow.tsx` — hover actions + archived variant
- `desktop/renderer/src/layout/SessionsPanel.tsx` — "archived" filter tab
- `desktop/renderer/src/layout/TargetsPanel.tsx` — "Show archived" toggle + per-row hover actions

**Frontend (create):**
- `desktop/renderer/src/modals/ArchiveConfirmModal.tsx`
- `desktop/renderer/src/modals/DeleteConfirmModal.tsx`
- `desktop/tests/e2e/delete-archive.spec.ts`

---

## Conventions used in this plan

- **Working directory:** repo root (`/Users/jrizzo/Projects/gitea/johnrizzo1/reverser`).
- **Test command:** `pytest <path> -v` from the repo root, with the project's normal devenv shell active (fastapi + httpx already installed).
- **Frontend type-check:** `cd desktop && npm run lint` (which is `tsc -b --noEmit`).
- **Trash entry format:** `targets/.trash/<ISO-timestamp-with-hyphens>-<target-name>/`, where the timestamp uses `strftime("%Y-%m-%dT%H-%M-%S")` (no colons — same format `make_session_id()` already uses, so it's filesystem-safe everywhere).

---

## Task 1: Add `archived_at` field to SessionSnapshot

**Files:**
- Modify: `src/reverser/sessions.py` (SessionSnapshot dataclass; `_from_dict()`)
- Create: `tests/test_session_archive_delete.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_archive_delete.py`:

```python
"""Helper coverage for archive/delete on SessionSnapshot."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_archived_at_defaults_to_none(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import SessionConfig, new_snapshot

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    assert snap.archived_at is None


def test_archived_at_round_trips_through_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig,
        SessionSnapshot,
        load,
        save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-14T10-00-00",
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        state="stopped",
        started_at="2026-05-14T10:00:00",
        last_active_at="2026-05-14T10:00:00",
        config=SessionConfig(profile="manager"),
        archived_at="2026-05-14T11:00:00+00:00",
    )
    save(snap)
    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.archived_at == "2026-05-14T11:00:00+00:00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_session_archive_delete.py -v`
Expected: FAIL — `SessionSnapshot.__init__()` does not accept `archived_at` (or attribute does not exist).

- [ ] **Step 3: Add the field to SessionSnapshot and _from_dict**

In `src/reverser/sessions.py`, find the `SessionSnapshot` dataclass (around line 86) and add `archived_at` after `stopped_at`:

```python
@dataclass
class SessionSnapshot:
    session_id: str                 # 2026-05-09T14-23-00
    target: str
    log_path: str
    state: SessionState
    started_at: str
    last_active_at: str
    stopped_at: Optional[str] = None
    archived_at: Optional[str] = None

    config: SessionConfig = field(
        default_factory=lambda: SessionConfig(profile="general")
    )
    ...
```

Update `_from_dict` (around line 266) to read `archived_at`:

```python
def _from_dict(d: dict) -> SessionSnapshot:
    """Reconstruct a SessionSnapshot from a dict (the inverse of asdict)."""
    config_data = d.get("config", {})
    stats_data = d.get("stats", {})
    ui_data = d.get("ui", {})
    in_flight_data = d.get("in_flight")
    conversation_data = d.get("conversation", [])

    return SessionSnapshot(
        session_id=d["session_id"],
        target=d["target"],
        log_path=d["log_path"],
        state=d["state"],
        started_at=d["started_at"],
        last_active_at=d.get("last_active_at", d["started_at"]),
        stopped_at=d.get("stopped_at"),
        archived_at=d.get("archived_at"),
        config=SessionConfig(**config_data) if config_data else SessionConfig(profile="general"),
        stats=SessionStats(**stats_data) if stats_data else SessionStats(),
        conversation=[ConversationEntry(**e) for e in conversation_data],
        ui=UIState(**ui_data) if ui_data else UIState(),
        in_flight=InFlightDispatch(**in_flight_data) if in_flight_data else None,
        pid=d.get("pid"),
        schema_version=d.get("schema_version", 1),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session_archive_delete.py -v`
Expected: 2 passed.

- [ ] **Step 5: Verify the existing test suite still passes**

Run: `pytest tests/test_sessions_module.py -v`
Expected: all pass (the field is optional so old snapshots load fine).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/sessions.py tests/test_session_archive_delete.py
git commit -m "feat(sessions): add archived_at field to SessionSnapshot"
```

---

## Task 2: Add `set_archived()` and `delete()` helpers

**Files:**
- Modify: `src/reverser/sessions.py` (new top-level functions)
- Modify: `tests/test_session_archive_delete.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_archive_delete.py`:

```python
def test_set_archived_writes_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, load, new_snapshot, save, set_archived,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    set_archived("10.10.10.5", snap.session_id, True)
    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.archived_at is not None
    assert reloaded.archived_at.startswith("20")  # looks like an ISO timestamp


def test_set_archived_false_clears_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, load, new_snapshot, save, set_archived,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)
    set_archived("10.10.10.5", snap.session_id, True)
    set_archived("10.10.10.5", snap.session_id, False)
    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.archived_at is None


def test_delete_unlinks_snapshot_and_log(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from reverser.sessions import (
        SessionConfig, SessionNotFoundError, delete, load,
        new_snapshot, save, snapshot_path,
    )

    log_path = tmp_path / "logs" / "x.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("event\n")

    snap = new_snapshot(
        target="10.10.10.5",
        log_path=str(log_path),
        config=SessionConfig(profile="manager"),
    )
    snap.state = "stopped"  # not active
    save(snap)

    delete("10.10.10.5", snap.session_id)

    assert not snapshot_path("10.10.10.5", snap.session_id).exists()
    assert not log_path.exists()
    with pytest.raises(SessionNotFoundError):
        load("10.10.10.5", snap.session_id)


def test_delete_is_ok_when_log_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, delete, new_snapshot, save, snapshot_path,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path=str(tmp_path / "logs" / "missing.jsonl"),  # never created
        config=SessionConfig(profile="manager"),
    )
    snap.state = "stopped"
    save(snap)
    # Should not raise even though the log doesn't exist
    delete("10.10.10.5", snap.session_id)
    assert not snapshot_path("10.10.10.5", snap.session_id).exists()


def test_delete_raises_on_active_session(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, SessionStateError, delete, new_snapshot, save,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    # new_snapshot sets state="active" by default
    save(snap)
    with pytest.raises(SessionStateError):
        delete("10.10.10.5", snap.session_id)


def test_set_archived_raises_on_active_session(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, SessionStateError, new_snapshot, save, set_archived,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)  # state == "active"
    with pytest.raises(SessionStateError):
        set_archived("10.10.10.5", snap.session_id, True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_session_archive_delete.py -v`
Expected: FAIL — `set_archived` and `delete` are not importable.

- [ ] **Step 3: Add helpers to sessions.py**

In `src/reverser/sessions.py`, append after the `is_session_alive` function (end of file):

```python
def set_archived(target: str, session_id: str, archived: bool) -> None:
    """Set or clear the archived_at timestamp on an existing snapshot.

    Loads, mutates, saves. Refuses if the snapshot's state is "active"
    (callers should stop the session first). Idempotent — setting True on
    an already-archived snapshot rewrites the timestamp; setting False on
    an unarchived snapshot is a no-op write.
    """
    snap = load(target, session_id)
    if snap.state == "active":
        raise SessionStateError(
            f"cannot archive an active session ({session_id}); stop it first"
        )
    snap.archived_at = _now_iso() if archived else None
    save(snap)


def delete(target: str, session_id: str) -> None:
    """Unlink a snapshot and its log file. Refuses if state == 'active'.

    The log file is best-effort: if it doesn't exist or is unreadable we
    log a warning and continue. The snapshot delete is the primary effect.
    """
    import logging

    snap = load(target, session_id)
    if snap.state == "active":
        raise SessionStateError(
            f"cannot delete an active session ({session_id}); stop it first"
        )

    snap_path = snapshot_path(target, session_id)
    log_path = Path(snap.log_path) if snap.log_path else None

    try:
        if log_path is not None and log_path.is_file():
            log_path.unlink()
    except OSError as e:
        logging.getLogger(__name__).warning(
            "failed to unlink session log %s: %s", log_path, e
        )

    snap_path.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session_archive_delete.py -v`
Expected: all 8 pass (the 2 from Task 1 + 6 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/sessions.py tests/test_session_archive_delete.py
git commit -m "feat(sessions): add set_archived and delete helpers"
```

---

## Task 3: Session archive + delete HTTP routes

**Files:**
- Modify: `src/reverser/gui_service/routes/sessions.py`
- Create: `tests/gui_service/test_session_archive_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_session_archive_routes.py`:

```python
"""Tests for archive/unarchive/hard-delete on session snapshots."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from reverser.sessions import (
    SessionConfig, SessionSnapshot, save,
)
from tests.gui_service.fakes import FakeBackend


HEADERS = {"Authorization": "Bearer t"}


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(host="127.0.0.1", port=0, token="t",
                         project_root=str(tmp_path))


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


def _persist_stopped_snapshot(tmp_path, target="10.10.10.5",
                              sid="2026-05-14T10-00-00"):
    """Write a stopped snapshot directly to disk (bypasses SessionManager)."""
    log = tmp_path / "logs" / f"{sid}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("event\n")
    snap = SessionSnapshot(
        session_id=sid, target=target, log_path=str(log),
        state="stopped",
        started_at="2026-05-14T10:00:00", last_active_at="2026-05-14T10:00:00",
        config=SessionConfig(profile="manager"),
    )
    save(snap)
    return snap


@pytest.mark.asyncio
async def test_archive_session_204(client, tmp_path):
    snap = _persist_stopped_snapshot(tmp_path)
    r = await client.post(
        f"/api/sessions/{snap.session_id}/archive?target={snap.target}",
        headers=HEADERS,
    )
    assert r.status_code == 204, r.text
    rows = (await client.get("/api/sessions", headers=HEADERS)).json()["sessions"]
    row = next(x for x in rows if x["id"] == snap.session_id)
    assert row["archived_at"] is not None


@pytest.mark.asyncio
async def test_unarchive_session_204(client, tmp_path):
    snap = _persist_stopped_snapshot(tmp_path)
    await client.post(
        f"/api/sessions/{snap.session_id}/archive?target={snap.target}",
        headers=HEADERS,
    )
    r = await client.delete(
        f"/api/sessions/{snap.session_id}/archive?target={snap.target}",
        headers=HEADERS,
    )
    assert r.status_code == 204
    rows = (await client.get("/api/sessions", headers=HEADERS)).json()["sessions"]
    row = next(x for x in rows if x["id"] == snap.session_id)
    assert row["archived_at"] is None


@pytest.mark.asyncio
async def test_delete_session_204_removes_files(client, tmp_path):
    snap = _persist_stopped_snapshot(tmp_path)
    log_path = tmp_path / "logs" / f"{snap.session_id}.jsonl"
    snap_file = (tmp_path / "targets" / snap.target / "sessions" /
                 f"{snap.session_id}.json")
    assert log_path.exists()
    assert snap_file.exists()

    r = await client.delete(
        f"/api/sessions/{snap.session_id}?target={snap.target}",
        headers=HEADERS,
    )
    assert r.status_code == 204
    assert not log_path.exists()
    assert not snap_file.exists()


@pytest.mark.asyncio
async def test_archive_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        target = create.json()["target"]

    r = await client.post(
        f"/api/sessions/{sid}/archive?target={target}",
        headers=HEADERS,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        target = create.json()["target"]

    r = await client.delete(
        f"/api/sessions/{sid}?target={target}",
        headers=HEADERS,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_archive_missing_session_returns_404(client):
    r = await client.post(
        "/api/sessions/nope/archive?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_session_returns_404(client):
    r = await client.delete(
        "/api/sessions/nope?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui_service/test_session_archive_routes.py -v`
Expected: FAIL — routes don't exist yet (404 from "no such route" or 405 method-not-allowed).

- [ ] **Step 3: Add routes**

In `src/reverser/gui_service/routes/sessions.py`, near the top extend the imports:

```python
from ...sessions import SessionNotFoundError, SessionStateError
from ...sessions import delete as delete_snapshot
from ...sessions import load as load_snapshot
from ...sessions import set_archived as set_snapshot_archived
```

Append to the end of the file:

```python
@router.post("/api/sessions/{session_id}/archive", status_code=204)
def archive_session(session_id: str, target: str, request: Request) -> Response:
    # Check the in-memory active session first — the on-disk snapshot may
    # not reflect the running state yet.
    mgr = _manager(request)
    if mgr.active is not None and mgr.active.session_id == session_id:
        raise HTTPException(409, detail="session is active; stop it first")
    try:
        set_snapshot_archived(target, session_id, True)
    except SessionNotFoundError:
        raise HTTPException(404)
    except SessionStateError as e:
        raise HTTPException(409, detail=str(e))
    return Response(status_code=204)


@router.delete("/api/sessions/{session_id}/archive", status_code=204)
def unarchive_session(session_id: str, target: str) -> Response:
    try:
        set_snapshot_archived(target, session_id, False)
    except SessionNotFoundError:
        raise HTTPException(404)
    return Response(status_code=204)


@router.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, target: str, request: Request) -> Response:
    mgr = _manager(request)
    if mgr.active is not None and mgr.active.session_id == session_id:
        raise HTTPException(409, detail="session is active; stop it first")
    try:
        delete_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404)
    except SessionStateError as e:
        raise HTTPException(409, detail=str(e))
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui_service/test_session_archive_routes.py -v`
Expected: 7 passed.

Note: the `archive_session` and `delete_session` 204 tests both call `GET /api/sessions` to check the row shape. That depends on Task 4 — for now, only the 204 status code part of those tests should pass. The `row["archived_at"] is not None` assertion will fail until Task 4 wires the field. Mark those two tests `@pytest.mark.skip(reason="row shape in Task 4")` if necessary, OR proceed to Task 4 and re-run.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/routes/sessions.py tests/gui_service/test_session_archive_routes.py
git commit -m "feat(api): add session archive/unarchive/delete endpoints"
```

---

## Task 4: Surface `archived_at` in SessionRow shape

**Files:**
- Modify: `src/reverser/gui_service/session_manager.py` (`list_sessions`)

- [ ] **Step 1: Write the failing test**

Append to `tests/gui_service/test_session_archive_routes.py`:

```python
@pytest.mark.asyncio
async def test_list_sessions_includes_archived_at_field(client, tmp_path):
    """Every session row must include archived_at (null by default)."""
    _persist_stopped_snapshot(tmp_path)
    rows = (await client.get("/api/sessions", headers=HEADERS)).json()["sessions"]
    assert rows, "expected at least one row"
    for row in rows:
        assert "archived_at" in row, f"row missing archived_at: {row}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/gui_service/test_session_archive_routes.py::test_list_sessions_includes_archived_at_field -v`
Expected: FAIL — KeyError on `archived_at` (or assert failure).

- [ ] **Step 3: Add the field to the SessionManager output**

In `src/reverser/gui_service/session_manager.py`, modify the `list_sessions` loop (around line 165) to include `archived_at`:

```python
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
                "archived_at": s.archived_at,
            })
```

The active-override `row.update({...})` only touches the keys it specifies, so the on-disk `archived_at` is preserved on the merged row.

Also update `_serialize` (around line 192) so that a fresh active session with no on-disk snapshot yet still produces a row with the field:

```python
    @staticmethod
    def _serialize(gs: GUISession) -> dict[str, Any]:
        s = gs.stats
        return {
            "id": gs.session_id,
            "state": "active",
            "target": s["target"],
            "profile": s["profile_key"],
            "turns": s["turns"],
            "total_cost": s["total_cost"],
            "budget": s["budget"],
            "max_turns": s["max_turns"],
            "archived_at": None,
        }
```

- [ ] **Step 4: Run the full archive-route suite to verify**

Run: `pytest tests/gui_service/test_session_archive_routes.py -v`
Expected: 8 passed (including the round-trip "archived → row archived_at populated" assertions from Task 3).

If you used `@pytest.mark.skip` in Task 3, remove those decorators now and rerun to confirm all pass.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/session_manager.py tests/gui_service/test_session_archive_routes.py
git commit -m "feat(api): include archived_at in /api/sessions rows"
```

---

## Task 5: Target archive + soft-delete HTTP routes + trash prune

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Create: `tests/gui_service/test_target_archive_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_target_archive_routes.py`:

```python
"""Tests for target archive/unarchive/soft-delete + trash prune."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from reverser.sessions import (
    SessionConfig, SessionSnapshot, save,
)
from tests.gui_service.fakes import FakeBackend


HEADERS = {"Authorization": "Bearer t"}


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(host="127.0.0.1", port=0, token="t",
                         project_root=str(tmp_path))


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "targets" / "10.10.10.5").mkdir(parents=True)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_archive_target_writes_marker(client, tmp_path):
    r = await client.post("/api/targets/10.10.10.5/archive", headers=HEADERS)
    assert r.status_code == 204
    assert (tmp_path / "targets" / "10.10.10.5" / ".archived").is_file()

    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    row = next(t for t in rows if t["name"] == "10.10.10.5")
    assert row["archived"] is True


@pytest.mark.asyncio
async def test_unarchive_target_removes_marker(client, tmp_path):
    await client.post("/api/targets/10.10.10.5/archive", headers=HEADERS)
    r = await client.delete("/api/targets/10.10.10.5/archive", headers=HEADERS)
    assert r.status_code == 204
    assert not (tmp_path / "targets" / "10.10.10.5" / ".archived").exists()

    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    row = next(t for t in rows if t["name"] == "10.10.10.5")
    assert row["archived"] is False


@pytest.mark.asyncio
async def test_soft_delete_target_moves_to_trash(client, tmp_path):
    r = await client.delete("/api/targets/10.10.10.5", headers=HEADERS)
    assert r.status_code == 204
    assert not (tmp_path / "targets" / "10.10.10.5").exists()

    trash = tmp_path / "targets" / ".trash"
    assert trash.is_dir()
    entries = list(trash.iterdir())
    assert len(entries) == 1
    # Filename: <YYYY-MM-DDTHH-MM-SS>-10.10.10.5
    name = entries[0].name
    assert name.endswith("-10.10.10.5"), f"unexpected trash entry name: {name}"

    # And subsequent GET /api/targets does not list the deleted target
    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    names = {t["name"] for t in rows}
    assert "10.10.10.5" not in names


@pytest.mark.asyncio
async def test_archive_target_with_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "targets" / "10.10.10.5"),
            "profile": "general", "backend": "claude",
            "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })

    r = await client.post("/api/targets/10.10.10.5/archive", headers=HEADERS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_soft_delete_target_with_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "targets" / "10.10.10.5"),
            "profile": "general", "backend": "claude",
            "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })

    r = await client.delete("/api/targets/10.10.10.5", headers=HEADERS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_archive_missing_target_returns_404(client):
    r = await client.post("/api/targets/does-not-exist/archive", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trash_prune_sweeps_old_entries(client, tmp_path):
    """Pre-seed .trash/ with a 31-day-old entry. Next GET /api/targets removes it."""
    trash = tmp_path / "targets" / ".trash"
    trash.mkdir(parents=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H-%M-%S")
    old_entry = trash / f"{old_ts}-someoldtarget"
    old_entry.mkdir()
    (old_entry / "marker.txt").write_text("payload")

    # Also seed a fresh entry that should survive
    fresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    fresh_entry = trash / f"{fresh_ts}-fresh"
    fresh_entry.mkdir()

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200

    assert not old_entry.exists(), "31-day-old entry should have been pruned"
    assert fresh_entry.exists(), "fresh entry should remain"


@pytest.mark.asyncio
async def test_trash_prune_ignores_unparseable_names(client, tmp_path):
    """Entries that don't start with an ISO timestamp are left alone."""
    trash = tmp_path / "targets" / ".trash"
    trash.mkdir(parents=True)
    weird = trash / "not-a-timestamp-anything"
    weird.mkdir()

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    assert weird.exists()


@pytest.mark.asyncio
async def test_list_targets_includes_archived_field(client):
    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    assert rows, "expected the seeded target"
    for t in rows:
        assert "archived" in t, f"row missing 'archived': {t}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui_service/test_target_archive_routes.py -v`
Expected: FAIL — routes don't exist; `archived` not in /api/targets shape.

- [ ] **Step 3: Add routes and prune logic**

In `src/reverser/gui_service/routes/targets.py`, near the top of the file extend imports:

```python
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
```

After `_targets_root()` (around line 49), add helpers:

```python
_TRASH_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-(.+)$")
_TRASH_RETENTION = timedelta(days=30)


def _trash_dir() -> Path:
    return _targets_root() / ".trash"


def _prune_trash(now: datetime | None = None) -> None:
    """Remove entries older than _TRASH_RETENTION. Silently ignores entries
    whose names don't start with an ISO timestamp."""
    trash = _trash_dir()
    if not trash.is_dir():
        return
    cutoff = (now or datetime.now(timezone.utc)) - _TRASH_RETENTION
    log = logging.getLogger(__name__)
    for entry in trash.iterdir():
        m = _TRASH_RE.match(entry.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if ts < cutoff:
            try:
                shutil.rmtree(entry)
            except OSError as e:
                log.warning("failed to prune trash entry %s: %s", entry, e)


def _has_active_session(target: str) -> bool:
    """True iff any snapshot for this target has state == 'active'."""
    return any(
        s.state == "active"
        for s in list_all_snapshots()
        if s.target == target
    )
```

Replace `list_targets` (around line 51) to include `archived` and prune trash:

```python
@router.get("/api/targets")
def list_targets() -> dict:
    _prune_trash()
    root = _targets_root()
    if not root.is_dir():
        return {"targets": []}
    targets = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        targets.append({
            "name": child.name,
            "has_kb": (child / "state.db").is_file(),
            "has_scope": (child / "scope.toml").is_file(),
            "archived": (child / ".archived").is_file(),
        })
    return {"targets": targets}
```

Append the three new endpoints to the same file:

```python
@router.post("/api/targets/{target}/archive", status_code=204)
def archive_target(target: str) -> Response:
    target_dir = _targets_root() / target
    if not target_dir.is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    if _has_active_session(target):
        raise HTTPException(409, detail="target has an active session; stop it first")
    marker = target_dir / ".archived"
    marker.write_text(
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    return Response(status_code=204)


@router.delete("/api/targets/{target}/archive", status_code=204)
def unarchive_target(target: str) -> Response:
    target_dir = _targets_root() / target
    if not target_dir.is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    marker = target_dir / ".archived"
    if marker.exists():
        marker.unlink()
    return Response(status_code=204)


@router.delete("/api/targets/{target}", status_code=204)
def delete_target(target: str) -> Response:
    target_dir = _targets_root() / target
    if not target_dir.is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    if _has_active_session(target):
        raise HTTPException(409, detail="target has an active session; stop it first")

    trash = _trash_dir()
    trash.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    dest = trash / f"{stamp}-{target}"
    # Avoid collision (two deletions in the same second)
    suffix = 0
    while dest.exists():
        suffix += 1
        dest = trash / f"{stamp}-{target}.{suffix}"
    shutil.move(str(target_dir), str(dest))
    return Response(status_code=204)
```

The existing `from fastapi import APIRouter, HTTPException, Response` import at the top of the file is sufficient — these handlers don't need `Request`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui_service/test_target_archive_routes.py -v`
Expected: 9 passed.

- [ ] **Step 5: Run the full backend suite to catch regressions**

Run: `pytest tests/ -v -k "not test_handshake_full_engagement_smoke"`
Expected: all pre-existing tests still pass. (The skipped test is environmentally flaky per project history.)

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_target_archive_routes.py
git commit -m "feat(api): add target archive/unarchive/soft-delete + trash prune"
```

---

## Task 6: Frontend types and React Query hooks

**Files:**
- Modify: `desktop/renderer/src/api/client.ts`
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Add fields to client types**

In `desktop/renderer/src/api/client.ts`:

Edit the `SessionRow` type (around line 72):

```typescript
export type SessionRow = {
  id: string;
  target: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  turns: number;
  total_cost: number;
  stopped_at: string | null;
  archived_at: string | null;
  budget?: number;
  max_turns?: number;
};
```

Edit the `TargetRow` type (around line 109):

```typescript
export type TargetRow = {
  name: string;
  has_kb: boolean;
  has_scope: boolean;
  archived: boolean;
};
```

- [ ] **Step 2: Add hooks to queries.ts**

In `desktop/renderer/src/api/queries.ts`, append these six hooks to the end of the file:

```typescript
// ---- Phase 4 (delete & archive): session-level mutations ----

export function useArchiveSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, target }: { sessionId: string; target: string }) =>
      api.post<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}/archive` +
        `?target=${encodeURIComponent(target)}`,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useUnarchiveSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, target }: { sessionId: string; target: string }) =>
      api.del<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}/archive` +
        `?target=${encodeURIComponent(target)}`,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, target }: { sessionId: string; target: string }) =>
      api.del<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}` +
        `?target=${encodeURIComponent(target)}`,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}

export function useArchiveTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.post<void>(`/api/targets/${encodeURIComponent(name)}/archive`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["targets"] }); },
  });
}

export function useUnarchiveTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.del<void>(`/api/targets/${encodeURIComponent(name)}/archive`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["targets"] }); },
  });
}

export function useDeleteTarget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.del<void>(`/api/targets/${encodeURIComponent(name)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["targets"] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
}
```

- [ ] **Step 3: Type-check**

Run: `cd desktop && npm run lint`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/api/client.ts desktop/renderer/src/api/queries.ts
git commit -m "feat(desktop): add archive/delete types and React Query hooks"
```

---

## Task 7: Generic Archive + Delete confirm modals

**Files:**
- Create: `desktop/renderer/src/modals/ArchiveConfirmModal.tsx`
- Create: `desktop/renderer/src/modals/DeleteConfirmModal.tsx`

- [ ] **Step 1: Create the archive confirm modal**

Create `desktop/renderer/src/modals/ArchiveConfirmModal.tsx`:

```tsx
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export function ArchiveConfirmModal({
  open, onOpenChange, title, description, onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  onConfirm: () => Promise<void> | void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          onClick={async () => {
            await onConfirm();
            onOpenChange(false);
          }}
        >
          Archive
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 2: Create the delete confirm modal**

Create `desktop/renderer/src/modals/DeleteConfirmModal.tsx`:

```tsx
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export function DeleteConfirmModal({
  open, onOpenChange, title, description, onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  onConfirm: () => Promise<void> | void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          onClick={async () => {
            await onConfirm();
            onOpenChange(false);
          }}
        >
          Delete
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd desktop && npm run lint`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/modals/ArchiveConfirmModal.tsx desktop/renderer/src/modals/DeleteConfirmModal.tsx
git commit -m "feat(desktop): add generic Archive/Delete confirm modals"
```

---

## Task 8: SessionRow — hover actions + archived variant

**Files:**
- Modify: `desktop/renderer/src/components/SessionRow.tsx`

- [ ] **Step 1: Rewrite SessionRow**

Replace the contents of `desktop/renderer/src/components/SessionRow.tsx`:

```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import { Archive, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SessionRow as SessionRowData } from "@/api/client";
import {
  useArchiveSession,
  useDeleteSession,
  useUnarchiveSession,
} from "@/api/queries";
import { ArchiveConfirmModal } from "@/modals/ArchiveConfirmModal";
import { DeleteConfirmModal } from "@/modals/DeleteConfirmModal";

const STATE_DOT: Record<SessionRowData["state"], string> = {
  active: "text-green-400",
  stopped: "text-amber-400",
  completed: "text-blue-400",
  abandoned: "text-neutral-500",
};

const STATE_GLYPH: Record<SessionRowData["state"], string> = {
  active: "●",
  stopped: "⏸",
  completed: "✓",
  abandoned: "—",
};

function _formatTime(iso: string | null): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!m) return iso;
  return `${m[1].slice(5)} ${m[2]}`;
}

function _formatArchivedDate(iso: string): string {
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : iso;
}

export function SessionRow({
  session,
  isActive = false,
}: {
  session: SessionRowData;
  isActive?: boolean;
}) {
  const t = _formatTime(session.stopped_at ?? null);
  const archived = session.archived_at !== null;
  const liveActive = session.state === "active";

  const archiveMutation = useArchiveSession();
  const unarchiveMutation = useUnarchiveSession();
  const deleteMutation = useDeleteSession();

  const [showArchive, setShowArchive] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="relative group">
      <Link
        to={`/sessions/${session.id}`}
        className={cn(
          "block px-3 py-2 border-l-2 transition-colors",
          isActive
            ? "border-neutral-300 bg-neutral-800/60"
            : "border-transparent hover:bg-neutral-900",
          archived && "bg-neutral-950/60 opacity-75",
        )}
      >
        <div className="flex items-center gap-2 text-xs">
          <span className={STATE_DOT[session.state]}>
            {archived ? "■" : STATE_GLYPH[session.state]}
          </span>
          <span className="text-neutral-200 truncate">{session.target}</span>
        </div>
        <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
          <span>{session.profile}</span>
          <span>·</span>
          <span>{t}</span>
          <span>·</span>
          <span>${session.total_cost.toFixed(2)}</span>
          {archived && (
            <>
              <span>·</span>
              <span className="text-neutral-400">
                archived {_formatArchivedDate(session.archived_at!)}
              </span>
            </>
          )}
        </div>
      </Link>

      {/* Hover-revealed row actions */}
      <div
        className={cn(
          "absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1",
          "opacity-0 group-hover:opacity-100 transition-opacity",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {archived ? (
          <button
            title="Restore"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              unarchiveMutation.mutate({
                sessionId: session.id,
                target: session.target,
              });
            }}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            title={liveActive ? "Stop the session first" : "Archive"}
            disabled={liveActive}
            className={cn(
              "p-1 rounded text-neutral-300",
              liveActive
                ? "opacity-30 cursor-not-allowed"
                : "hover:bg-neutral-700",
            )}
            onClick={(e) => {
              e.preventDefault();
              if (!liveActive) setShowArchive(true);
            }}
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
        )}

        <div className="relative">
          <button
            title="More"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              setMenuOpen((v) => !v);
            }}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-10 min-w-[180px] rounded border border-neutral-700 bg-neutral-900 shadow-lg text-xs"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <button
                disabled={liveActive}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left",
                  liveActive
                    ? "text-neutral-500 cursor-not-allowed"
                    : "text-red-400 hover:bg-neutral-800",
                )}
                onClick={(e) => {
                  e.preventDefault();
                  setMenuOpen(false);
                  if (!liveActive) setShowDelete(true);
                }}
                title={liveActive ? "Stop the session first" : undefined}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete permanently
              </button>
            </div>
          )}
        </div>
      </div>

      <ArchiveConfirmModal
        open={showArchive}
        onOpenChange={setShowArchive}
        title="Archive this session?"
        description="This hides it from the default view. You can find it in the 'archived' filter and restore at any time."
        onConfirm={() =>
          archiveMutation.mutateAsync({
            sessionId: session.id,
            target: session.target,
          })
        }
      />
      <DeleteConfirmModal
        open={showDelete}
        onOpenChange={setShowDelete}
        title="Delete this session permanently?"
        description="The snapshot and its log file will be removed from disk. This can't be undone."
        onConfirm={() =>
          deleteMutation.mutateAsync({
            sessionId: session.id,
            target: session.target,
          })
        }
      />
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npm run lint`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/components/SessionRow.tsx
git commit -m "feat(desktop): add archive/delete hover actions to SessionRow"
```

---

## Task 9: SessionsPanel — add "archived" filter tab

**Files:**
- Modify: `desktop/renderer/src/layout/SessionsPanel.tsx`

- [ ] **Step 1: Update the filter type, list, and filtering logic**

Replace the contents of `desktop/renderer/src/layout/SessionsPanel.tsx`:

```tsx
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useSessions } from "@/api/queries";
import { SessionRow } from "@/components/SessionRow";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Filter =
  | "all"
  | "active"
  | "stopped"
  | "completed"
  | "abandoned"
  | "archived";

const FILTERS: Filter[] = [
  "all", "active", "stopped", "completed", "abandoned", "archived",
];

export function SessionsPanel() {
  const { id: routeId } = useParams<{ id: string }>();
  const sessions = useSessions();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const all = sessions.data?.sessions ?? [];

  // "all" excludes archived by default — archived has its own tab.
  const visible = useMemo(
    () => all.filter((s) => s.archived_at === null),
    [all],
  );

  const counts = useMemo(() => {
    const c: Record<Filter, number> = {
      all: visible.length,
      active: 0, stopped: 0, completed: 0, abandoned: 0,
      archived: 0,
    };
    for (const s of all) {
      if (s.archived_at !== null) c.archived += 1;
      else c[s.state] += 1;
    }
    return c;
  }, [all, visible.length]);

  const filtered = useMemo(() => {
    let rows: typeof all;
    if (filter === "archived") {
      rows = all.filter((s) => s.archived_at !== null);
    } else if (filter === "all") {
      rows = visible;
    } else {
      rows = visible.filter((s) => s.state === filter);
    }
    const q = query.trim().toLowerCase();
    if (q) {
      rows = rows.filter((s) =>
        s.target.toLowerCase().includes(q) ||
        s.profile.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q),
      );
    }
    return rows.slice().sort((a, b) => {
      if (a.state === "active" && b.state !== "active") return -1;
      if (b.state === "active" && a.state !== "active") return 1;
      return (b.stopped_at ?? "").localeCompare(a.stopped_at ?? "");
    });
  }, [all, visible, filter, query]);

  return (
    <div className="h-full flex flex-col bg-neutral-950 border-r border-neutral-800">
      <div className="p-3 border-b border-neutral-800">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
          Sessions
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] mb-2">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "transition-colors",
                f === filter
                  ? "text-neutral-100 border-b border-neutral-100"
                  : "text-neutral-500 hover:text-neutral-300",
              )}
            >
              {f} ({counts[f]})
            </button>
          ))}
        </div>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter target / profile / id…"
          className="h-7 text-xs"
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {filtered.length === 0 ? (
          <p className="p-3 text-xs text-neutral-500">
            {all.length === 0 ? "no sessions yet" : "no matches"}
          </p>
        ) : (
          filtered.map((s) => (
            <SessionRow key={s.id} session={s} isActive={s.id === routeId} />
          ))
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npm run lint`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/layout/SessionsPanel.tsx
git commit -m "feat(desktop): add archived filter tab to SessionsPanel"
```

---

## Task 10: TargetsPanel — Show archived toggle + per-row hover actions

**Files:**
- Modify: `desktop/renderer/src/layout/TargetsPanel.tsx`

- [ ] **Step 1: Rewrite TargetsPanel**

Replace the contents of `desktop/renderer/src/layout/TargetsPanel.tsx`:

```tsx
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Archive, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react";
import {
  useArchiveTarget,
  useDeleteTarget,
  useSessions,
  useTargets,
  useUnarchiveTarget,
} from "@/api/queries";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ArchiveConfirmModal } from "@/modals/ArchiveConfirmModal";
import { DeleteConfirmModal } from "@/modals/DeleteConfirmModal";

type Sort = "activity" | "name";

type Row = {
  name: string;
  archived: boolean;
  sessions: number;
  total_cost: number;
  last_activity: string;
  any_active: boolean;
};

function TargetRow({
  r,
  active,
}: {
  r: Row;
  active: boolean;
}) {
  const archiveMutation = useArchiveTarget();
  const unarchiveMutation = useUnarchiveTarget();
  const deleteMutation = useDeleteTarget();

  const [showArchive, setShowArchive] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="relative group">
      <Link
        to={`/target/${encodeURIComponent(r.name)}`}
        className={cn(
          "block px-3 py-2 border-l-2 transition-colors",
          active
            ? "border-neutral-300 bg-neutral-800/60"
            : "border-transparent hover:bg-neutral-900",
          r.archived && "bg-neutral-950/60 opacity-75",
        )}
      >
        <div className="text-xs text-neutral-200 truncate">{r.name}</div>
        <div className="text-[10px] text-neutral-500 font-mono mt-0.5 flex gap-2">
          <span className={r.any_active ? "text-green-400" : ""}>
            {r.any_active ? "● active" : r.archived ? "■" : "○"}
          </span>
          <span>·</span>
          <span>
            {r.sessions} session{r.sessions === 1 ? "" : "s"}
          </span>
          <span>·</span>
          <span>${r.total_cost.toFixed(2)}</span>
          {r.archived && (
            <>
              <span>·</span>
              <span className="text-neutral-400">archived</span>
            </>
          )}
        </div>
      </Link>

      <div
        className={cn(
          "absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1",
          "opacity-0 group-hover:opacity-100 transition-opacity",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {r.archived ? (
          <button
            title="Restore"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              unarchiveMutation.mutate(r.name);
            }}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            title={r.any_active ? "Stop the session first" : "Archive"}
            disabled={r.any_active}
            className={cn(
              "p-1 rounded text-neutral-300",
              r.any_active
                ? "opacity-30 cursor-not-allowed"
                : "hover:bg-neutral-700",
            )}
            onClick={(e) => {
              e.preventDefault();
              if (!r.any_active) setShowArchive(true);
            }}
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
        )}

        <div className="relative">
          <button
            title="More"
            className="p-1 rounded hover:bg-neutral-700 text-neutral-300"
            onClick={(e) => {
              e.preventDefault();
              setMenuOpen((v) => !v);
            }}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-10 min-w-[180px] rounded border border-neutral-700 bg-neutral-900 shadow-lg text-xs"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <button
                disabled={r.any_active}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left",
                  r.any_active
                    ? "text-neutral-500 cursor-not-allowed"
                    : "text-red-400 hover:bg-neutral-800",
                )}
                onClick={(e) => {
                  e.preventDefault();
                  setMenuOpen(false);
                  if (!r.any_active) setShowDelete(true);
                }}
                title={r.any_active ? "Stop the session first" : undefined}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete permanently
              </button>
            </div>
          )}
        </div>
      </div>

      <ArchiveConfirmModal
        open={showArchive}
        onOpenChange={setShowArchive}
        title="Archive this target?"
        description="This hides it from the default view. You can find it via the 'Show archived' toggle and restore at any time."
        onConfirm={() => archiveMutation.mutateAsync(r.name)}
      />
      <DeleteConfirmModal
        open={showDelete}
        onOpenChange={setShowDelete}
        title="Delete this target?"
        description="The directory will be moved to targets/.trash/ for 30 days. It won't appear in the UI. Recovery requires filesystem access; after 30 days the trash entry is pruned."
        onConfirm={() => deleteMutation.mutateAsync(r.name)}
      />
    </div>
  );
}

export function TargetsPanel() {
  const { name: routeName } = useParams<{ name: string }>();
  const targets = useTargets();
  const sessions = useSessions();
  const [sort, setSort] = useState<Sort>("activity");
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  const rows: Row[] = useMemo(() => {
    const list = targets.data?.targets ?? [];
    const sess = sessions.data?.sessions ?? [];

    const summarized: Row[] = list.map((t) => {
      const ts = sess.filter((s) => s.target === t.name);
      const last =
        ts
          .map((s) => s.stopped_at ?? "")
          .filter(Boolean)
          .sort()
          .at(-1) ?? "";
      const totalCost = ts.reduce((acc, s) => acc + (s.total_cost ?? 0), 0);
      const anyActive = ts.some((s) => s.state === "active");
      return {
        name: t.name,
        archived: t.archived,
        sessions: ts.length,
        total_cost: totalCost,
        last_activity: last,
        any_active: anyActive,
      };
    });

    const visible = showArchived
      ? summarized
      : summarized.filter((r) => !r.archived);

    const q = query.trim().toLowerCase();
    let filtered = q
      ? visible.filter((r) => r.name.toLowerCase().includes(q))
      : visible;

    filtered = filtered.slice().sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      return (b.last_activity ?? "").localeCompare(a.last_activity ?? "");
    });

    return filtered;
  }, [targets.data, sessions.data, sort, query, showArchived]);

  return (
    <div className="h-full flex flex-col bg-neutral-950 border-r border-neutral-800">
      <div className="p-3 border-b border-neutral-800">
        <div className="text-[10px] uppercase tracking-wide text-neutral-500 mb-2">
          Targets
        </div>
        <div className="flex items-center gap-3 text-[10px] mb-2">
          <button
            onClick={() => setSort("activity")}
            className={cn(
              sort === "activity"
                ? "text-neutral-100 border-b border-neutral-100"
                : "text-neutral-500 hover:text-neutral-300",
            )}
          >
            by activity
          </button>
          <button
            onClick={() => setSort("name")}
            className={cn(
              sort === "name"
                ? "text-neutral-100 border-b border-neutral-100"
                : "text-neutral-500 hover:text-neutral-300",
            )}
          >
            by name
          </button>
          <label className="ml-auto flex items-center gap-1 text-neutral-400 cursor-pointer">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="h-3 w-3"
            />
            Show archived
          </label>
        </div>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter…"
          className="h-7 text-xs"
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {rows.length === 0 ? (
          <p className="p-3 text-xs text-neutral-500">no targets yet</p>
        ) : (
          rows.map((r) => (
            <TargetRow key={r.name} r={r} active={r.name === routeName} />
          ))
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npm run lint`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/layout/TargetsPanel.tsx
git commit -m "feat(desktop): add archive/delete hover actions + Show archived toggle to TargetsPanel"
```

---

## Task 11: Playwright structural tests

**Files:**
- Create: `desktop/tests/e2e/delete-archive.spec.ts`

- [ ] **Step 1: Write the e2e tests**

Create `desktop/tests/e2e/delete-archive.spec.ts`:

```typescript
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// Plan 8 structural tests: confirm the new UI elements mount without
// breaking the existing flow. Real fixture-driven assertions (clicking
// Archive opens the modal and the row disappears from the default list)
// require a pre-seeded targets directory and an attached service — out
// of scope for these structural tests.

test("sessions panel renders the archived filter tab", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click('[title="Sessions"]');
    await expect(w.locator("text=/^archived \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("targets panel renders the Show archived toggle", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click('[title="Targets"]');
    await expect(w.locator("text=Show archived")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("sessions panel still shows the all filter (regression)", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click('[title="Sessions"]');
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("targets panel still shows the by activity sort (regression)", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click('[title="Targets"]');
    await expect(w.locator("text=by activity")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});
```

- [ ] **Step 2: Build the desktop bundle so dist-electron/main.js exists**

Run: `cd desktop && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Run the e2e tests**

Run: `cd desktop && npx playwright test tests/e2e/delete-archive.spec.ts`
Expected: 4 passed.

If you see "Chromium not installed", run `npx playwright install chromium` first.

- [ ] **Step 4: Sanity-run the full Playwright suite**

Run: `cd desktop && npm run test:e2e`
Expected: all phases (smoke, engagement, phase2, phase3a, phase3b, delete-archive) pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/tests/e2e/delete-archive.spec.ts
git commit -m "test(desktop): add structural e2e tests for delete/archive UI"
```

---

## Task 12: Full-suite verification and final commit

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full backend test suite**

Run: `pytest tests/ -v -k "not test_handshake_full_engagement_smoke"`
Expected: all pass. (The skipped test is environmentally flaky per project history; do not change it as part of this work.)

- [ ] **Step 2: Run the desktop type-check**

Run: `cd desktop && npm run lint`
Expected: clean.

- [ ] **Step 3: Run the full Playwright suite**

Run: `cd desktop && npm run test:e2e`
Expected: all pass.

- [ ] **Step 4: Confirm no uncommitted changes**

Run: `git status`
Expected: working tree clean (or only untracked files from prior sessions like `.claude/`, `.playwright-mcp/`, `register-page.png` — none of these should be in this branch's commits).

- [ ] **Step 5: Review the commit log for this feature**

Run: `git log --oneline -15`
Expected: a contiguous sequence of feature commits (one per task), ending with this verification task.
