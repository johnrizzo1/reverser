# Phase 2 Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two read-only endpoints Phase 2 needs: `GET /api/targets/{name}/summary` (rolled-up per-target stats) and `GET /api/sessions/conversation/{id}?target={t}` (snapshot conversation for read-only chat replay).

**Architecture:** Both endpoints sit alongside the existing `routes/targets.py` and `routes/sessions.py` modules. They reuse the existing KB + sessions stores — no new persistence, no new auth model. Returns 404 for missing target dirs / unknown snapshots; sits behind the same bearer-token dependency as the rest of `/api/*`.

**Tech Stack:** FastAPI, the existing `reverser.kb` and `reverser.sessions` modules, pytest with `httpx.AsyncClient`.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-phase-2-sessions-targets-design.md`](../specs/2026-05-13-phase-2-sessions-targets-design.md) — sections 7 and 10.

---

## File map

```
src/reverser/gui_service/routes/targets.py             modify  (add /summary handler)
src/reverser/gui_service/routes/sessions.py            modify  (add /conversation handler)
tests/gui_service/test_targets_summary.py              create
tests/gui_service/test_conversation.py                 create
```

---

## Task 1: `GET /api/targets/{name}/summary`

Rolls up `sessions.list_all()` and `kb.for_target()` into a single response. Used by the TargetsPanel rows and the TargetOverview summary card.

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Test: `tests/gui_service/test_targets_summary.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_targets_summary.py`:

```python
"""GET /api/targets/{name}/summary rolls up per-target sessions + KB stats."""
import json
import pytest
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    (tmp_path / "targets" / "10.10.10.5").mkdir(parents=True)
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_summary_for_empty_target(client, tmp_path):
    """A target dir with no sessions and no KB returns all-zero counts."""
    r = await client.get("/api/targets/10.10.10.5/summary", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "10.10.10.5"
    assert body["sessions"]["total"] == 0
    assert body["sessions"]["by_state"] == {
        "active": 0, "stopped": 0, "completed": 0, "abandoned": 0,
    }
    assert body["spend"]["total_usd"] == 0.0
    assert body["profiles_used"] == []
    assert body["first_activity"] is None
    assert body["last_activity"] is None
    for key in ("hosts", "services", "credentials",
                "findings", "hypotheses", "artifacts", "notes"):
        assert body["kb_counts"][key] == 0


@pytest.mark.asyncio
async def test_summary_404_when_target_dir_missing(client):
    r = await client.get("/api/targets/no-such-target/summary", headers=HEADERS)
    assert r.status_code == 404


def _write_snapshot(tmp_path, target, session_id, state, profile, cost, started_at, stopped_at=None):
    """Write a SessionSnapshot JSON the way reverser.sessions.save does."""
    target_dir = tmp_path / "targets" / target / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "version": 1,
        "session_id": session_id,
        "target": target,
        "log_path": str(tmp_path / "logs" / f"{target}.jsonl"),
        "config": {
            "profile": profile,
            "backend": "claude",
            "model": None,
            "api_base": None,
            "budget": 5.0,
            "max_turns": 50,
        },
        "stats": {
            "turns": 3,
            "total_cost": cost,
        },
        "state": state,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "pid": None,
        "conversation": [],
    }
    (target_dir / f"{session_id}.json").write_text(json.dumps(snap))


@pytest.mark.asyncio
async def test_summary_aggregates_session_states_and_cost(client, tmp_path):
    """Three sessions for a target — counts roll up by state, costs sum."""
    _write_snapshot(tmp_path, "10.10.10.5", "2026-05-09T18-22-00",
                    "completed", "general", 0.50,
                    "2026-05-09T18:22:00Z", "2026-05-09T19:00:00Z")
    _write_snapshot(tmp_path, "10.10.10.5", "2026-05-11T09-15-00",
                    "stopped", "manager", 0.30,
                    "2026-05-11T09:15:00Z", "2026-05-11T10:00:00Z")
    _write_snapshot(tmp_path, "10.10.10.5", "2026-05-13T11-04-00",
                    "active", "manager", 0.62,
                    "2026-05-13T11:04:00Z", None)

    r = await client.get("/api/targets/10.10.10.5/summary", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sessions"]["total"] == 3
    assert body["sessions"]["by_state"] == {
        "active": 1, "stopped": 1, "completed": 1, "abandoned": 0,
    }
    assert abs(body["spend"]["total_usd"] - (0.50 + 0.30 + 0.62)) < 1e-6
    # Two distinct profiles; "manager" used twice so listed first.
    assert body["profiles_used"] == ["manager", "general"]
    assert body["first_activity"] == "2026-05-09T18:22:00Z"
    # Latest activity = most recent started_at (active) or stopped_at.
    assert body["last_activity"] == "2026-05-13T11:04:00Z"
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_targets_summary.py -v`
Expected: FAIL — `404 Not Found` on the summary endpoint (the route doesn't exist yet).

- [ ] **Step 3: Implement the `/summary` route**

Edit `src/reverser/gui_service/routes/targets.py`. After the existing `read_kb` handler, append the new handler and a small helper:

```python
from collections import Counter

from ...sessions import list_all as list_all_snapshots


def _summarize_target(target: str) -> dict:
    """Roll up snapshots and KB counts for one target. Caller must have
    already verified that the target directory exists."""
    snapshots = [s for s in list_all_snapshots() if s.target == target]

    by_state = {"active": 0, "stopped": 0, "completed": 0, "abandoned": 0}
    profile_counts: Counter[str] = Counter()
    total_cost = 0.0
    started_values = []
    last_values = []

    for s in snapshots:
        # Defensive: an unknown state slot would silently disappear without this.
        if s.state in by_state:
            by_state[s.state] += 1
        profile_counts[s.config.profile] += 1
        total_cost += float(s.stats.total_cost or 0.0)
        if s.started_at:
            started_values.append(s.started_at)
        # last_activity: stopped_at if present, else started_at (for active sessions).
        last_values.append(s.stopped_at or s.started_at)

    first_activity = min(started_values) if started_values else None
    last_activity = max(v for v in last_values if v) if any(last_values) else None
    profiles_used = [p for p, _ in profile_counts.most_common()]

    # KB counts via the existing list helpers. Use len() since the store
    # doesn't expose dedicated count_* methods.
    try:
        kb = for_target(target)
    except Exception:
        kb = None

    def _count(method_name: str) -> int:
        if kb is None:
            return 0
        fn = getattr(kb, method_name, None)
        if fn is None:
            return 0
        try:
            return len(list(fn()))
        except Exception:
            return 0

    kb_counts = {
        "hosts":       _count("get_hosts"),
        "services":    _count("get_services"),
        "credentials": _count("get_credentials"),
        "findings":    _count("get_findings"),
        "hypotheses":  _count("list_hypotheses"),
        "artifacts":   _count("get_artifacts"),
        "notes":       _count("get_notes"),
    }

    return {
        "target": target,
        "sessions": {"total": len(snapshots), "by_state": by_state},
        "spend": {"total_usd": round(total_cost, 6)},
        "profiles_used": profiles_used,
        "first_activity": first_activity,
        "last_activity": last_activity,
        "kb_counts": kb_counts,
    }


@router.get("/api/targets/{target}/summary")
def read_summary(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    return _summarize_target(target)
```

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_targets_summary.py -v`
Expected: PASS — three tests.

- [ ] **Step 5: Full suite to confirm no regressions**

Run: `pytest tests/gui_service/ -v`
Expected: PASS — all pre-existing tests plus the three new ones (so 49 passing assuming the existing 46 baseline).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_targets_summary.py
git commit -m "feat(gui_service): /api/targets/{name}/summary rolls up sessions + KB stats"
```

---

## Task 2: `GET /api/sessions/conversation/{id}?target={t}`

Returns the snapshot's `conversation` list so the frontend can replay chat history when opening a non-active session.

**Files:**
- Modify: `src/reverser/gui_service/routes/sessions.py`
- Test: `tests/gui_service/test_conversation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_conversation.py`:

```python
"""GET /api/sessions/conversation/{id}?target=t serves a snapshot's
conversation history for the frontend's read-only chat replay."""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


def _write_snapshot_with_conversation(tmp_path, target, session_id, conversation):
    """Write a SessionSnapshot JSON with the given conversation history."""
    target_dir = tmp_path / "targets" / target / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "version": 1,
        "session_id": session_id,
        "target": target,
        "log_path": str(tmp_path / "logs" / f"{target}.jsonl"),
        "config": {
            "profile": "webpentest", "backend": "claude", "model": None,
            "api_base": None, "budget": 5.0, "max_turns": 50,
        },
        "stats": {"turns": len(conversation), "total_cost": 0.30},
        "state": "stopped",
        "started_at": "2026-05-12T22:54:46Z",
        "stopped_at": "2026-05-12T23:14:00Z",
        "pid": None,
        "conversation": conversation,
    }
    (target_dir / f"{session_id}.json").write_text(json.dumps(snap))


@pytest.mark.asyncio
async def test_conversation_returns_history(client, tmp_path):
    convo = [
        {"user": "look at the login form", "agent": "Found 3 fields…",
         "turn": 1, "timestamp": "2026-05-12T22:55:14Z", "cost": 0.12},
        {"user": "try SQLi", "agent": "sqlmap negative…",
         "turn": 2, "timestamp": "2026-05-12T23:00:00Z", "cost": 0.18},
    ]
    _write_snapshot_with_conversation(
        tmp_path, "app.example.com", "2026-05-12T22-54-46", convo,
    )

    r = await client.get(
        "/api/sessions/conversation/2026-05-12T22-54-46?target=app.example.com",
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "2026-05-12T22-54-46"
    assert body["target"] == "app.example.com"
    assert body["profile"] == "webpentest"
    assert body["state"] == "stopped"
    assert len(body["conversation"]) == 2
    assert body["conversation"][0]["user"] == "look at the login form"
    assert body["conversation"][1]["turn"] == 2


@pytest.mark.asyncio
async def test_conversation_404_for_unknown_session(client):
    r = await client.get(
        "/api/sessions/conversation/missing?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_conversation_requires_target_query(client):
    r = await client.get(
        "/api/sessions/conversation/some-id",
        headers=HEADERS,
    )
    # FastAPI maps missing required query params to 422 (unprocessable entity).
    assert r.status_code == 422
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_conversation.py -v`
Expected: FAIL — `404 Not Found` (route absent).

- [ ] **Step 3: Implement the `/conversation` route**

Edit `src/reverser/gui_service/routes/sessions.py`. Find the existing imports at the top of the file and add `load` from `...sessions`:

```python
from ...sessions import load as load_snapshot
```

Then append the new handler after the existing `resume_session` (or any other handler — order doesn't matter):

```python
@router.get("/api/sessions/conversation/{session_id}")
def get_conversation(session_id: str, target: str) -> dict:
    """Return a snapshot's conversation history for read-only replay.

    `target` is required because reverser.sessions.load takes both args
    (sessions are scoped per target). The frontend knows the target from
    the SessionsPanel row it clicked.
    """
    try:
        snap = load_snapshot(target, session_id)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"unknown session: {session_id!r}")
    return {
        "id": snap.session_id,
        "target": snap.target,
        "profile": snap.config.profile,
        "state": snap.state,
        "conversation": [
            {
                "user": e.user,
                "agent": e.agent,
                "turn": e.turn,
                "timestamp": e.timestamp,
                "cost": e.cost,
            }
            for e in snap.conversation
        ],
    }
```

Note on the 404 path: `reverser.sessions.load` raises `FileNotFoundError` if the snapshot JSON doesn't exist. If it raises something else in practice (verify via grep), wrap the exception type accordingly — the test asserts 404 on the missing case.

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_conversation.py -v`
Expected: PASS — three tests.

- [ ] **Step 5: Full suite**

Run: `pytest tests/gui_service/ -v`
Expected: PASS — all pre-existing tests + Task 1's 3 new + Task 2's 3 new.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/sessions.py tests/gui_service/test_conversation.py
git commit -m "feat(gui_service): /api/sessions/conversation/{id} for chat history replay"
```

---

## Verification

```bash
pytest tests/gui_service/ -v
```

Expected: all green. New tests should show 6 added (3 + 3).

Manual smoke (from inside the devenv shell, with the service running):

```bash
# Spawn the service in one shell:
python -m reverser.gui_service --port 0 --project-root .

# Capture port + token from the handshake JSON line, then in another shell:
PORT=… TOKEN=…
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:$PORT/api/targets/10.10.10.5/summary" | python3 -m json.tool

# If you have a session for app.example.com:
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:$PORT/api/sessions/conversation/<id>?target=app.example.com" | python3 -m json.tool
```

Both should return well-formed JSON with the documented shape.

## What this plan does NOT cover

- All frontend changes (panels, target page, read-only mode, router restructure) — see Plan 5.
- Live model-list probing for backends (Phase 4).
- Per-target cost limits — out of scope.
