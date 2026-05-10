# Stop & Resume Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stop and resume capability to interactive `reverser` sessions so a long-running engagement can be suspended and picked up later, possibly in a different process or on a different day, with operator state, conversation history, and minimal UI hints all restored.

**Architecture:** A new `src/reverser/sessions.py` module owns a `SessionSnapshot` dataclass plus save/load/listing helpers. Snapshots persist to `targets/<target>/sessions/<id>.json` with autosave per turn, an explicit stop button (`f3` / `/stop`), and a guaranteed snapshot on TUI exit (atexit + SIGTERM handlers). The Session class gains a `resume_from` constructor parameter and `stop()` / `mark_completed()` methods. CLI gains a top-level `--list-sessions` flag and a `--resume [SESSION_ID]` argument on `interactive`.

**Tech Stack:** Python 3.10+, Textual TUI, `dataclasses` + `json` from stdlib, `asyncio`, `contextvars`. No new external dependencies.

**Spec:** `docs/superpowers/specs/2026-05-09-stop-resume-design.md`

**Baseline:** Verify with `pytest tests/ -q` before starting. As of this writing on `main`, baseline is **320 passing tests + 1 skipped**. If the `feature/manager-profile` work has been merged before you start, baseline will be ~372 instead — adjust expected counts accordingly throughout the plan.

**Manager-profile coupling:** Tasks 16 and 17 modify `src/reverser/tools/dispatch.py` and `src/reverser/profiles/manager.py`, which only exist if the manager profile work has landed. Both tasks include guards: if the file doesn't exist, the task is a no-op (skip and proceed). Re-do the task when manager-profile is merged.

---

## File structure

### Phase 1 — Sessions module (Tasks 1-4)

**Create:**
- `src/reverser/sessions.py` — snapshot dataclasses, save/load, listing helpers, ContextVar
- `tests/test_sessions_module.py`

### Phase 2 — Session class refactor (Tasks 5-9)

**Modify:**
- `src/reverser/tui/session.py` — `Exchange` dataclass, `findings` → `exchanges` refactor, split `__init__` into `_init_new` / `_init_resumed`, add `resume_from` parameter, add `stop()` / `mark_completed()` / autosave hook, set ContextVar
- `src/reverser/session_log.py` — new event kinds: `session_resumed`, `session_stopped`, `session_completed` with corresponding `log_*` methods

**Create:**
- `tests/test_session_resume.py`
- `tests/test_session_lifecycle.py`

### Phase 3 — TUI surface (Tasks 10-13)

**Create:**
- `src/reverser/tui/modals/__init__.py`
- `src/reverser/tui/modals/stop_confirm.py`
- `src/reverser/tui/modals/done_confirm.py`
- `src/reverser/tui/modals/info.py`

**Modify:**
- `src/reverser/tui/app.py` — keybindings (`f3`, `f4`, `f5`), slash command interceptor, atexit + SIGTERM handlers, header rendering with session info, chat-pane resume replay

### Phase 4 — CLI (Tasks 14-15)

**Modify:**
- `src/reverser/cli.py` — top-level `--list-sessions` flag, `--resume [SESSION_ID]` on interactive, resume-aware argument resolution

**Create:**
- `tests/test_cli_sessions.py`

### Phase 5 — Manager integration (Tasks 16-17, conditional)

**Modify (only if file exists):**
- `src/reverser/tools/dispatch.py` — read `current_session` ContextVar, mutate `in_flight` on dispatch start/end, honor cancel signal with 5-second grace
- `src/reverser/profiles/manager.py` — `SKILL_WRAPUP` prompt update

**Create (only if dispatch.py exists):**
- `tests/test_dispatch_in_flight.py`

### Phase 6 — Smoke + validation (Tasks 18-19)

**Create:**
- `tests/manual/resume_smoke.md`

---

## Task 1: Sessions module skeleton + dataclasses

**Files:**
- Create: `src/reverser/sessions.py`
- Create: `tests/test_sessions_module.py`

This task establishes the data shapes (snapshot + nested dataclasses) and the module shell. No I/O yet — that's Task 2.

- [ ] **Step 1: Write failing tests for the dataclass surface**

Create `tests/test_sessions_module.py`:

```python
"""Unit tests for the sessions module — snapshot dataclasses + helpers."""

import pytest

from reverser.sessions import (
    SessionSnapshot,
    SessionConfig,
    SessionStats,
    ConversationEntry,
    UIState,
    InFlightDispatch,
    make_session_id,
    new_snapshot,
)


def test_make_session_id_format():
    """Session IDs are filename-safe ISO timestamps."""
    sid = make_session_id()
    # Format: YYYY-MM-DDTHH-MM-SS (colons replaced with hyphens for filename safety)
    assert len(sid) == 19
    assert sid[10] == "T"
    assert sid[4] == "-" and sid[7] == "-"
    assert sid[13] == "-" and sid[16] == "-"
    # Verify filename safety
    assert ":" not in sid
    assert "/" not in sid


def test_session_config_defaults():
    """SessionConfig has sensible defaults requiring only profile."""
    c = SessionConfig(profile="general")
    assert c.profile == "general"
    assert c.backend == "claude"
    assert c.model is None
    assert c.api_base is None
    assert c.budget == 5.0
    assert c.max_turns == 50
    assert c.max_parallel == 1


def test_session_stats_defaults():
    s = SessionStats()
    assert s.total_cost == 0.0
    assert s.turns == 0


def test_conversation_entry_required_fields():
    e = ConversationEntry(
        user="hello",
        agent="hi",
        turn=1,
        timestamp="2026-05-09T14:23:00Z",
        cost=0.01,
    )
    assert e.user == "hello"
    assert e.cost == 0.01


def test_ui_state_defaults():
    u = UIState()
    assert u.focused_panel == "chat"
    assert u.chat_scroll_position == 0
    assert u.last_skill_key is None
    assert u.input_buffer == ""


def test_in_flight_dispatch_shape():
    f = InFlightDispatch(
        kind="dispatch",
        specialty="ad",
        hypothesis_id=5,
        sub_goal="Verify SMB signing",
        started_at="2026-05-09T14:23:00Z",
    )
    assert f.kind == "dispatch"
    assert f.hypothesis_id == 5


def test_new_snapshot_starts_active_with_pid(tmp_path, monkeypatch):
    """new_snapshot() returns a fresh SessionSnapshot with state=active and current pid."""
    import os
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    assert snap.session_id  # non-empty
    assert snap.target == "10.10.10.5"
    assert snap.log_path == "logs/test.jsonl"
    assert snap.state == "active"
    assert snap.config.profile == "manager"
    assert snap.pid == os.getpid()
    assert snap.started_at  # set
    assert snap.last_active_at  # set
    assert snap.stopped_at is None
    assert snap.in_flight is None
    assert snap.conversation == []
    assert snap.schema_version == 1


def test_snapshot_serializes_to_dict():
    """Snapshots round-trip through dataclasses.asdict for JSON encoding."""
    from dataclasses import asdict
    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00Z",
        last_active_at="2026-05-09T14:23:00Z",
        config=SessionConfig(profile="manager"),
    )
    d = asdict(snap)
    assert d["session_id"] == "2026-05-09T14-23-00"
    assert d["state"] == "active"
    assert d["config"]["profile"] == "manager"
    assert d["conversation"] == []
    assert d["pid"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.sessions'`.

- [ ] **Step 3: Create `src/reverser/sessions.py`**

```python
"""Session snapshot persistence — save/load/list resumable sessions.

Each interactive session has a snapshot file at
`targets/<target>/sessions/<session_id>.json` that captures everything
needed to resume the session: operator state (target, profile, budget,
cost-to-date), conversation history (user+agent+cost per exchange),
minimal UI hints (focused panel, scroll position), and any in-flight
dispatch state.

Lifecycle states:
- active:    session is running (or was running when last seen)
- stopped:   user invoked stop; intends to resume later
- completed: user invoked /done; terminal — not offered for resume by default
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from reverser.tui.session import Session


SessionState = Literal["active", "stopped", "completed"]
SCHEMA_VERSION = 1


@dataclass
class SessionConfig:
    profile: str
    backend: str = "claude"
    model: Optional[str] = None
    api_base: Optional[str] = None
    budget: float = 5.0
    max_turns: int = 50
    max_parallel: int = 1


@dataclass
class SessionStats:
    total_cost: float = 0.0
    turns: int = 0


@dataclass
class ConversationEntry:
    user: str
    agent: str
    turn: int
    timestamp: str   # ISO-8601
    cost: float      # USD spent on this exchange


@dataclass
class UIState:
    focused_panel: str = "chat"     # chat | log | status
    chat_scroll_position: int = 0
    last_skill_key: Optional[str] = None
    input_buffer: str = ""


@dataclass
class InFlightDispatch:
    kind: Literal["dispatch"]       # leaves room for future kinds
    specialty: str
    hypothesis_id: Optional[int]
    sub_goal: str
    started_at: str


@dataclass
class SessionSnapshot:
    session_id: str                 # 2026-05-09T14-23-00
    target: str
    log_path: str                   # relative to repo root
    state: SessionState
    started_at: str
    last_active_at: str
    stopped_at: Optional[str] = None

    config: SessionConfig = field(
        default_factory=lambda: SessionConfig(profile="general")
    )
    stats: SessionStats = field(default_factory=SessionStats)
    conversation: list[ConversationEntry] = field(default_factory=list)
    ui: UIState = field(default_factory=UIState)

    in_flight: Optional[InFlightDispatch] = None
    pid: Optional[int] = None       # set while running; cleared on clean stop

    schema_version: int = SCHEMA_VERSION


# ── ContextVar (used by Session-aware tools, e.g. dispatch_specialist) ──

current_session: ContextVar[Optional["Session"]] = ContextVar(
    "current_session", default=None
)


# ── Helpers ────────────────────────────────────────────────────────────


class SessionNotFoundError(Exception):
    """Raised when a snapshot file doesn't exist."""


class SessionStateError(Exception):
    """Raised when an operation conflicts with the snapshot's state.

    Examples: trying to resume a completed session; trying to take over a
    live session without --force.
    """


class SchemaError(Exception):
    """Raised on schema_version mismatch with no migration path."""


def _now_iso() -> str:
    """Return current time as ISO-8601 with microseconds, UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _targets_root() -> Path:
    """Resolve the targets/ directory from REVERSER_TARGETS_DIR or default."""
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


def make_session_id() -> str:
    """Filename-safe ISO timestamp for a session ID.

    Format: YYYY-MM-DDTHH-MM-SS (colons in the time portion replaced with
    hyphens because POSIX filenames can technically contain colons but
    several tools and shells choke on them).
    """
    now = datetime.now(timezone.utc)
    # 2026-05-09T14:23:00 -> 2026-05-09T14-23-00
    return now.strftime("%Y-%m-%dT%H-%M-%S")


def new_snapshot(
    *, target: str, log_path: str, config: SessionConfig
) -> SessionSnapshot:
    """Construct a fresh snapshot for a new session.

    state=active, pid=os.getpid(), timestamps populated.
    """
    now = _now_iso()
    return SessionSnapshot(
        session_id=make_session_id(),
        target=target,
        log_path=log_path,
        state="active",
        started_at=now,
        last_active_at=now,
        config=config,
        pid=os.getpid(),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v`

Expected: PASS (8 tests).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 328 passed (320 baseline + 8 new), 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/sessions.py tests/test_sessions_module.py
git commit -m "$(cat <<'EOF'
feat(sessions): add snapshot dataclass + module skeleton

New module reverser.sessions owns the SessionSnapshot dataclass and
nested types (SessionConfig, SessionStats, ConversationEntry, UIState,
InFlightDispatch). Includes the current_session ContextVar that
Session-aware tools (e.g. dispatch_specialist) will read to access the
running session.

I/O helpers (save/load/list) come in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Save/load with atomic writes

**Files:**
- Modify: `src/reverser/sessions.py` — add `snapshot_path`, `save`, `load`
- Modify: `tests/test_sessions_module.py` — add I/O tests

- [ ] **Step 1: Write failing tests**

Append to `tests/test_sessions_module.py`:

```python
def test_snapshot_path_uses_targets_dir(tmp_path, monkeypatch):
    """snapshot_path returns targets/<target>/sessions/<id>.json under REVERSER_TARGETS_DIR."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import snapshot_path

    p = snapshot_path("10.10.10.5", "2026-05-09T14-23-00")
    assert p == tmp_path / "10.10.10.5" / "sessions" / "2026-05-09T14-23-00.json"


def test_save_creates_directory_and_file(tmp_path, monkeypatch):
    """save() creates the target/sessions/ directory if missing and writes the file."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    expected = tmp_path / "10.10.10.5" / "sessions" / f"{snap.session_id}.json"
    assert expected.exists()
    # File is valid JSON
    import json
    data = json.loads(expected.read_text())
    assert data["session_id"] == snap.session_id
    assert data["state"] == "active"


def test_save_updates_last_active_at(tmp_path, monkeypatch):
    """save() bumps last_active_at to now before serializing."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, load, new_snapshot, SessionConfig
    import time

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    original_last_active = snap.last_active_at
    time.sleep(1.1)  # ensure ISO timestamp (second precision) advances
    save(snap)

    loaded = load("10.10.10.5", snap.session_id)
    assert loaded.last_active_at != original_last_active


def test_save_is_atomic(tmp_path, monkeypatch):
    """save() never leaves a partially-written file at the canonical path.

    We check that the canonical path exists ONLY after rename. A .tmp file
    may be left behind on crash, but the canonical path is always either
    the previous valid snapshot or the new valid snapshot — never partial.
    """
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    canonical = tmp_path / "10.10.10.5" / "sessions" / f"{snap.session_id}.json"
    tmp_files = list((tmp_path / "10.10.10.5" / "sessions").glob("*.tmp"))

    assert canonical.exists()
    assert tmp_files == []  # cleaned up after successful rename


def test_load_round_trip(tmp_path, monkeypatch):
    """A saved snapshot loads back to an equivalent dataclass."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, load, new_snapshot, SessionConfig, ConversationEntry,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager", budget=10.0),
    )
    snap.conversation = [
        ConversationEntry(user="hi", agent="hello", turn=1,
                          timestamp="2026-05-09T14:23:00", cost=0.01),
        ConversationEntry(user="next", agent="ok", turn=2,
                          timestamp="2026-05-09T14:24:00", cost=0.02),
    ]
    snap.stats.total_cost = 0.03
    snap.stats.turns = 2
    save(snap)

    loaded = load("10.10.10.5", snap.session_id)
    assert loaded.session_id == snap.session_id
    assert loaded.target == snap.target
    assert loaded.config.profile == "manager"
    assert loaded.config.budget == 10.0
    assert loaded.stats.total_cost == 0.03
    assert loaded.stats.turns == 2
    assert len(loaded.conversation) == 2
    assert loaded.conversation[0].user == "hi"
    assert loaded.conversation[1].cost == 0.02


def test_load_raises_session_not_found_on_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import load, SessionNotFoundError

    with pytest.raises(SessionNotFoundError):
        load("10.10.10.5", "nonexistent-session-id")


def test_load_raises_schema_error_on_bad_version(tmp_path, monkeypatch):
    """A snapshot with an unknown schema_version errors instead of silently loading."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import load, SchemaError, snapshot_path
    import json

    p = snapshot_path("10.10.10.5", "future-session")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "session_id": "future-session",
        "target": "10.10.10.5",
        "log_path": "logs/x.jsonl",
        "state": "active",
        "started_at": "2030-01-01T00:00:00",
        "last_active_at": "2030-01-01T00:00:00",
        "schema_version": 99,
    }))

    with pytest.raises(SchemaError):
        load("10.10.10.5", "future-session")


def test_save_overwrites_existing(tmp_path, monkeypatch):
    """save() updates an existing snapshot file in place."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, load, new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)
    snap.stats.turns = 5
    save(snap)

    loaded = load("10.10.10.5", snap.session_id)
    assert loaded.stats.turns == 5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v`

Expected: 8 new tests fail with `ImportError: cannot import name 'snapshot_path' from 'reverser.sessions'` or similar.

- [ ] **Step 3: Add I/O helpers to `src/reverser/sessions.py`**

Append (before the helper class definitions or after — order doesn't matter, place after `new_snapshot`):

```python
def snapshot_path(target: str, session_id: str) -> Path:
    """Canonical path for a session snapshot file."""
    return _targets_root() / target / "sessions" / f"{session_id}.json"


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
        last_active_at=d["last_active_at"],
        stopped_at=d.get("stopped_at"),
        config=SessionConfig(**config_data) if config_data else SessionConfig(profile="general"),
        stats=SessionStats(**stats_data) if stats_data else SessionStats(),
        conversation=[ConversationEntry(**e) for e in conversation_data],
        ui=UIState(**ui_data) if ui_data else UIState(),
        in_flight=InFlightDispatch(**in_flight_data) if in_flight_data else None,
        pid=d.get("pid"),
        schema_version=d.get("schema_version", 1),
    )


def save(snapshot: SessionSnapshot) -> None:
    """Atomically write the snapshot to disk.

    Updates last_active_at to now before serialization. Writes to a
    sibling .tmp file then renames atomically; partially-written snapshots
    never appear at the canonical path.
    """
    snapshot.last_active_at = _now_iso()
    path = snapshot_path(snapshot.target, snapshot.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(asdict(snapshot), indent=2, sort_keys=False)
    tmp_path.write_text(payload)
    os.replace(tmp_path, path)


def load(target: str, session_id: str) -> SessionSnapshot:
    """Read and parse a snapshot file.

    Raises SessionNotFoundError if the file is missing.
    Raises SchemaError if schema_version is unknown.
    """
    path = snapshot_path(target, session_id)
    if not path.exists():
        raise SessionNotFoundError(
            f"No snapshot at {path}. Use --list-sessions to see available sessions."
        )
    data = json.loads(path.read_text())
    version = data.get("schema_version", 1)
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"Snapshot schema version {version} is not supported "
            f"(this reverser supports v{SCHEMA_VERSION}). "
            f"Either upgrade reverser or hand-edit the file at {path}."
        )
    return _from_dict(data)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v`

Expected: PASS (16 tests total — 8 from Task 1 + 8 new).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 336 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/sessions.py tests/test_sessions_module.py
git commit -m "$(cat <<'EOF'
feat(sessions): atomic save/load for snapshots

snapshot_path() resolves the canonical filesystem location.
save() does an atomic write via temp-file + os.replace so partially
written snapshots never appear at the canonical path. load() validates
schema_version and surfaces a SchemaError on mismatch (no silent
forward-compat).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Listing helpers (`list_for_target`, `list_all`, `latest_*`)

**Files:**
- Modify: `src/reverser/sessions.py`
- Modify: `tests/test_sessions_module.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_sessions_module.py`:

```python
def test_list_for_target_returns_snapshots_sorted_desc(tmp_path, monkeypatch):
    """list_for_target enumerates a target's session files, sorted by last_active_at desc."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, list_for_target, SessionSnapshot, SessionConfig,
    )

    older = SessionSnapshot(
        session_id="2026-05-08T09-12-44",
        target="10.10.10.5",
        log_path="logs/older.jsonl",
        state="stopped",
        started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="manager"),
    )
    newer = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/newer.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="manager"),
    )
    save(older)
    save(newer)

    snaps = list_for_target("10.10.10.5")
    assert len(snaps) == 2
    # save() bumps last_active_at; newer was saved second, so it's "more recent"
    assert snaps[0].session_id == "2026-05-09T14-23-00"
    assert snaps[1].session_id == "2026-05-08T09-12-44"


def test_list_for_target_empty_when_no_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import list_for_target

    assert list_for_target("nonexistent-target") == []


def test_list_for_target_excludes_completed_when_requested(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, list_for_target, SessionSnapshot, SessionConfig,
    )

    completed = SessionSnapshot(
        session_id="completed-1",
        target="10.10.10.5",
        log_path="logs/c.jsonl",
        state="completed",
        started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="manager"),
    )
    active = SessionSnapshot(
        session_id="active-1",
        target="10.10.10.5",
        log_path="logs/a.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="manager"),
    )
    save(completed)
    save(active)

    all_snaps = list_for_target("10.10.10.5")
    assert len(all_snaps) == 2

    only_resumable = list_for_target("10.10.10.5", exclude_completed=True)
    assert len(only_resumable) == 1
    assert only_resumable[0].session_id == "active-1"


def test_list_for_target_skips_tmp_files(tmp_path, monkeypatch):
    """Orphan .tmp files from interrupted writes are not returned by list."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, list_for_target, snapshot_path
    from reverser.sessions import new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)
    # Simulate orphan .tmp from a crashed save
    orphan = snapshot_path("10.10.10.5", "orphan-id").with_suffix(".json.tmp")
    orphan.write_text("{not even valid json")

    snaps = list_for_target("10.10.10.5")
    assert len(snaps) == 1  # the .tmp orphan is ignored


def test_list_all_walks_all_target_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, list_all, SessionSnapshot, SessionConfig,
    )

    snap_a = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/a.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="manager"),
    )
    snap_b = SessionSnapshot(
        session_id="2026-05-08T09-12-44",
        target="10.10.10.7",
        log_path="logs/b.jsonl",
        state="stopped",
        started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="ad"),
    )
    save(snap_a)
    save(snap_b)

    snaps = list_all()
    assert len(snaps) == 2
    targets = {s.target for s in snaps}
    assert targets == {"10.10.10.5", "10.10.10.7"}


def test_latest_for_target_picks_most_recent_resumable(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, latest_for_target, SessionSnapshot, SessionConfig,
    )
    import time

    older = SessionSnapshot(
        session_id="older",
        target="10.10.10.5",
        log_path="logs/o.jsonl",
        state="stopped",
        started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="manager"),
    )
    save(older)
    time.sleep(1.1)  # ensure last_active_at differs
    newer = SessionSnapshot(
        session_id="newer",
        target="10.10.10.5",
        log_path="logs/n.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="manager"),
    )
    save(newer)

    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "newer"


def test_latest_for_target_excludes_completed_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, latest_for_target, SessionSnapshot, SessionConfig,
    )

    completed = SessionSnapshot(
        session_id="completed-recent",
        target="10.10.10.5",
        log_path="logs/c.jsonl",
        state="completed",
        started_at="2026-05-09T15:00:00",
        last_active_at="2026-05-09T18:00:00",
        config=SessionConfig(profile="manager"),
    )
    older_stopped = SessionSnapshot(
        session_id="stopped-older",
        target="10.10.10.5",
        log_path="logs/s.jsonl",
        state="stopped",
        started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="manager"),
    )
    save(completed)
    save(older_stopped)

    # completed is "more recent" by save time but excluded → should pick stopped
    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "stopped-older"


def test_latest_for_target_returns_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import latest_for_target

    assert latest_for_target("nothing-here") is None


def test_latest_global_picks_most_recent_across_all_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, latest_global, SessionSnapshot, SessionConfig,
    )
    import time

    a = SessionSnapshot(
        session_id="a",
        target="10.10.10.5",
        log_path="logs/a.jsonl",
        state="stopped",
        started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="manager"),
    )
    save(a)
    time.sleep(1.1)
    b = SessionSnapshot(
        session_id="b",
        target="10.10.10.7",
        log_path="logs/b.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="ad"),
    )
    save(b)

    latest = latest_global()
    assert latest is not None
    assert latest.session_id == "b"
    assert latest.target == "10.10.10.7"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v 2>&1 | tail -15`

Expected: 9 new tests fail with `ImportError: cannot import name 'list_for_target'` (or similar for the other functions).

- [ ] **Step 3: Add listing helpers to `src/reverser/sessions.py`**

Append:

```python
def list_for_target(
    target: str, *, exclude_completed: bool = False
) -> list[SessionSnapshot]:
    """Enumerate snapshots for a target, sorted by last_active_at desc.

    Skips orphan .tmp files (incomplete writes from crashes).
    """
    sessions_dir = _targets_root() / target / "sessions"
    if not sessions_dir.is_dir():
        return []

    snapshots: list[SessionSnapshot] = []
    for entry in sessions_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue  # skip .tmp orphans, subdirs, anything non-JSON
        try:
            data = json.loads(entry.read_text())
            snap = _from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue  # skip corrupted snapshots silently
        if exclude_completed and snap.state == "completed":
            continue
        snapshots.append(snap)

    snapshots.sort(key=lambda s: s.last_active_at, reverse=True)
    return snapshots


def list_all(*, exclude_completed: bool = False) -> list[SessionSnapshot]:
    """Walk targets/*/sessions/, return all parsed snapshots, sorted desc."""
    root = _targets_root()
    if not root.is_dir():
        return []

    all_snaps: list[SessionSnapshot] = []
    for target_dir in root.iterdir():
        if not target_dir.is_dir():
            continue
        all_snaps.extend(
            list_for_target(target_dir.name, exclude_completed=exclude_completed)
        )

    all_snaps.sort(key=lambda s: s.last_active_at, reverse=True)
    return all_snaps


def latest_for_target(
    target: str, *, exclude_completed: bool = True
) -> Optional[SessionSnapshot]:
    """Most recent snapshot for the target.

    By default excludes completed sessions (they're terminal, not resumable).
    Returns None if no eligible session.
    """
    snaps = list_for_target(target, exclude_completed=exclude_completed)
    return snaps[0] if snaps else None


def latest_global(
    *, exclude_completed: bool = True
) -> Optional[SessionSnapshot]:
    """Most recent snapshot across all targets."""
    snaps = list_all(exclude_completed=exclude_completed)
    return snaps[0] if snaps else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v`

Expected: PASS (25 tests total).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 345 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/sessions.py tests/test_sessions_module.py
git commit -m "$(cat <<'EOF'
feat(sessions): listing + latest helpers

list_for_target / list_all enumerate snapshots, sorted by last_active_at
desc. latest_for_target / latest_global pick the most recent eligible
session, excluding completed sessions by default. Orphan .tmp files
(from interrupted atomic writes) and corrupted snapshot files are
silently skipped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Liveness check (`is_session_alive`)

**Files:**
- Modify: `src/reverser/sessions.py`
- Modify: `tests/test_sessions_module.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_sessions_module.py`:

```python
def test_is_session_alive_true_for_own_pid(tmp_path, monkeypatch):
    """is_session_alive returns True when pid is the current process."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import is_session_alive, new_snapshot, SessionConfig
    import os

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    # new_snapshot sets pid=os.getpid()
    assert snap.pid == os.getpid()
    assert is_session_alive(snap) is True


def test_is_session_alive_false_for_dead_pid(tmp_path, monkeypatch):
    """is_session_alive returns False when pid points to a non-existent process."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        is_session_alive, new_snapshot, SessionConfig,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    # PID 1 is init/launchd; almost certainly NOT a reverser process. We
    # use a high arbitrary number that's vanishingly unlikely to exist.
    snap.pid = 999999
    assert is_session_alive(snap) is False


def test_is_session_alive_false_when_pid_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        is_session_alive, new_snapshot, SessionConfig,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    snap.pid = None
    assert is_session_alive(snap) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py::test_is_session_alive_true_for_own_pid -v`

Expected: FAIL — `ImportError: cannot import name 'is_session_alive'`.

- [ ] **Step 3: Add `is_session_alive` to `src/reverser/sessions.py`**

Append:

```python
def is_session_alive(snapshot: SessionSnapshot) -> bool:
    """True iff snapshot.pid is set AND that process exists.

    Uses os.kill(pid, 0) — sends signal 0 (no-op probe). Raises
    OSError if the process doesn't exist; we catch that and return False.
    Note: this can be wrong if PID has been reused by an unrelated
    process. Liveness check is best-effort, not authoritative.
    """
    if snapshot.pid is None:
        return False
    try:
        os.kill(snapshot.pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_sessions_module.py -v`

Expected: PASS (28 tests total).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 348 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/sessions.py tests/test_sessions_module.py
git commit -m "$(cat <<'EOF'
feat(sessions): is_session_alive PID liveness check

Used by the resume code path to refuse takeover of a session whose
process is still running. Best-effort only (PID reuse can produce
false positives); --force will bypass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `Exchange` dataclass + Session.findings → Session.exchanges refactor

**Files:**
- Modify: `src/reverser/tui/session.py`
- Modify: any test file that probes `Session.findings` (audit first)

This is the riskiest internal refactor. The current `Session.findings: list[str]` becomes `Session.exchanges: list[Exchange]` where each Exchange tracks user/agent/turn/timestamp/cost. Every read of `findings` (the prompt builder mainly) needs to project from exchanges.

- [ ] **Step 1: Audit existing references to `findings`**

Run:
```bash
grep -rn "\.findings\|self.findings" src/ tests/ 2>/dev/null
```

Note every reference. Likely candidates: `Session._build_prompt` (builds the next prompt from recent findings), `Session._run_one_turn` (appends to findings), and any TUI panel that displays findings.

If the grep returns occurrences in tests, those tests need updating in this task.

- [ ] **Step 2: Write a failing test for the Exchange shape and projection**

Create `tests/test_session_exchanges.py`:

```python
"""Tests for the Session.exchanges (formerly findings) refactor."""

import pytest


def test_exchange_dataclass_shape():
    """Exchange has the fields needed for the snapshot."""
    from reverser.tui.session import Exchange

    e = Exchange(
        user="hi",
        agent="hello",
        turn=1,
        timestamp="2026-05-09T14:23:00",
        cost=0.01,
    )
    assert e.user == "hi"
    assert e.agent == "hello"
    assert e.turn == 1
    assert e.cost == 0.01


def test_session_exchanges_is_a_list_of_exchange(tmp_path, monkeypatch):
    """A new Session has an empty exchanges list of the right type."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session, Exchange
    from unittest.mock import MagicMock

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    assert isinstance(sess.exchanges, list)
    assert sess.exchanges == []


def test_recent_findings_strings_projects_from_exchanges(tmp_path, monkeypatch):
    """The prompt builder gets a list-of-strings projection of exchanges."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session, Exchange
    from unittest.mock import MagicMock

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    sess.exchanges = [
        Exchange(user="q1", agent="a1", turn=1,
                 timestamp="2026-05-09T14:23:00", cost=0.01),
        Exchange(user="q2", agent="a2", turn=2,
                 timestamp="2026-05-09T14:24:00", cost=0.02),
    ]
    findings = sess._recent_findings_strings()
    # The projection used by the prompt builder is the agent responses,
    # one per exchange (matching the old `findings` semantics).
    assert findings == ["a1", "a2"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_exchanges.py -v`

Expected: FAIL — `ImportError: cannot import name 'Exchange' from 'reverser.tui.session'`.

- [ ] **Step 4: Refactor `src/reverser/tui/session.py`**

Add the `Exchange` dataclass at module level (near the other dataclasses):

```python
from dataclasses import dataclass


@dataclass
class Exchange:
    """One agent ↔ user round-trip during a session."""
    user: str
    agent: str
    turn: int
    timestamp: str   # ISO-8601
    cost: float      # USD spent on this exchange
```

In `Session.__init__` (current `_init_new` after split, or before split for now), replace:

```python
self.findings: list[str] = []
```

with:

```python
self.exchanges: list[Exchange] = []
```

Add a projection helper:

```python
def _recent_findings_strings(self, limit: int = 8) -> list[str]:
    """Project recent exchanges into the list-of-strings the prompt builder expects.

    The current prompt builder uses the last 8 findings (agent responses)
    as conversation context. This preserves the existing semantics over
    the new Exchange-based storage.
    """
    return [e.agent for e in self.exchanges[-limit:]]
```

In `_build_prompt` (or wherever `self.findings` was iterated), replace `self.findings` with `self._recent_findings_strings()` (uses the default limit of 8). If the existing code took a slice like `self.findings[-8:]`, the new call needs no slice — the helper handles it.

In `_run_one_turn`, where the loop currently appends to `self.findings`, replace with appending an `Exchange`:

```python
# (after the turn completes, with user_message, turn_text_parts, etc. in scope)
self.exchanges.append(Exchange(
    user=user_message,
    agent="\n".join(turn_text_parts) if turn_text_parts else "",
    turn=self.stats.turns,
    timestamp=_now_iso_session(),  # use a session-local helper or inline datetime.now
    cost=last_event_cost or 0.0,    # the per-turn cost from the AgentEvent
))
```

The exact variable names (`user_message`, `turn_text_parts`, `last_event_cost`) depend on what's already in the function. **Inspect `_run_one_turn` first** and adapt.

If a `_now_iso_session` helper doesn't exist, define it at module level:

```python
from datetime import datetime, timezone

def _now_iso_session() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

(Or just inline the datetime call.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_exchanges.py -v`

Expected: PASS (3 tests).

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 351 passed, 1 skipped (348 + 3 new).

If existing tests fail because they probed `Session.findings` directly: update them to use `Session.exchanges` (and project to strings if they were checking string content). The audit in Step 1 should have flagged these.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tui/session.py tests/test_session_exchanges.py
git commit -m "$(cat <<'EOF'
refactor(session): findings → exchanges (Exchange dataclass with cost)

Internal refactor to track per-exchange metadata (user/agent/turn/
timestamp/cost) needed for snapshot persistence. The prompt builder
projects exchanges to strings via _recent_findings_strings() so the
existing prompt format is preserved.

No behavior change — same prompt, same agent loop, just with structured
storage underneath.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Session `resume_from` parameter + `_init_new` / `_init_resumed` split

**Files:**
- Modify: `src/reverser/tui/session.py`
- Create: `tests/test_session_resume.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session_resume.py`:

```python
"""Tests for resuming a Session from a SessionSnapshot."""

import os
import pytest
from unittest.mock import MagicMock


def test_session_init_new_creates_snapshot_on_disk(tmp_path, monkeypatch):
    """Constructing a fresh Session creates a snapshot file at active state."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.sessions import snapshot_path

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    p = snapshot_path("10.10.10.5", sess._snapshot.session_id)
    assert p.exists()
    assert sess._snapshot.state == "active"
    assert sess._snapshot.pid == os.getpid()


def test_session_init_resumed_restores_state_from_snapshot(tmp_path, monkeypatch):
    """A snapshot loaded into Session(... resume_from=snap) restores fields."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session, Exchange
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, SessionStats, ConversationEntry, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general", budget=10.0, max_turns=100),
        stats=SessionStats(total_cost=2.50, turns=42),
        conversation=[
            ConversationEntry(user="q1", agent="a1", turn=1,
                              timestamp="2026-05-09T14:23:00", cost=0.05),
            ConversationEntry(user="q2", agent="a2", turn=2,
                              timestamp="2026-05-09T14:24:00", cost=0.07),
        ],
    )
    save(snap)

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
        resume_from=snap,
    )

    assert sess.target == "10.10.10.5"
    assert sess.budget == 10.0
    assert sess.max_turns == 100
    assert sess.stats.total_cost == 2.50
    assert sess.stats.turns == 42
    assert len(sess.exchanges) == 2
    assert sess.exchanges[0].user == "q1"
    assert sess.exchanges[1].cost == 0.07
    # State flipped back to active; pid is now ours
    assert sess._snapshot.state == "active"
    assert sess._snapshot.pid == os.getpid()


def test_session_init_resumed_continues_writing_to_same_log(tmp_path, monkeypatch):
    """The resumed session reuses the snapshot's log_path; doesn't mint a new one."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/specific-log.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general"),
    )
    save(snap)

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
        resume_from=snap,
    )
    assert sess.log_path == "logs/specific-log.jsonl"


def test_session_init_resumed_rejects_profile_mismatch(tmp_path, monkeypatch):
    """If the caller passes a profile that doesn't match the snapshot, error."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general"),
    )
    save(snap)

    with pytest.raises(ValueError, match="profile"):
        Session(
            target="10.10.10.5",
            profile=get_profile("ad"),  # different profile
            backend=MagicMock(),
            resume_from=snap,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_resume.py -v`

Expected: FAIL — `TypeError: Session.__init__() got an unexpected keyword argument 'resume_from'` (and `_snapshot` attribute may not exist).

- [ ] **Step 3: Refactor `Session.__init__`**

In `src/reverser/tui/session.py`, refactor the constructor to support both new and resumed sessions. Add the `resume_from` parameter:

```python
from reverser.sessions import (
    SessionSnapshot, SessionConfig, ConversationEntry,
    new_snapshot, save as save_snapshot, current_session,
)


class Session:
    def __init__(
        self,
        target: str,
        profile: Profile,
        backend: Backend,
        *,
        budget: float = 5.0,
        max_turns: int = 50,
        max_parallel: int = 1,
        log_path: str | None = None,
        resume_from: SessionSnapshot | None = None,
    ):
        if resume_from is not None:
            self._init_resumed(resume_from, profile, backend)
        else:
            self._init_new(
                target=target,
                profile=profile,
                backend=backend,
                budget=budget,
                max_turns=max_turns,
                max_parallel=max_parallel,
                log_path=log_path,
            )
        # Set the ContextVar so session-aware tools can find us
        current_session.set(self)

    def _init_new(self, *, target, profile, backend, budget, max_turns,
                  max_parallel, log_path):
        """Original __init__ behavior — fresh session."""
        # ... (move the existing __init__ body here, with these adjustments at the end:)

        # Mint and persist the initial snapshot
        config = SessionConfig(
            profile=profile.key,
            budget=budget,
            max_turns=max_turns,
            max_parallel=max_parallel,
            # backend/model/api_base: pull from backend if available, else defaults
        )
        self._snapshot = new_snapshot(
            target=target,
            log_path=self._log_path,
            config=config,
        )
        save_snapshot(self._snapshot)

    def _init_resumed(self, snap: SessionSnapshot, profile: Profile, backend: Backend):
        """Restore session state from a snapshot."""
        if snap.config.profile != profile.key:
            raise ValueError(
                f"Cannot resume: snapshot profile is '{snap.config.profile}' "
                f"but caller passed profile '{profile.key}'. Drop the -p flag "
                f"to use the snapshot's profile, or start a new session."
            )

        self.target = snap.target
        self._is_url_target = self.target.startswith("http")
        self._is_web = profile.key in _WEB_PROFILES if "_WEB_PROFILES" in globals() else False
        self.profile = profile
        self.backend = backend
        self.budget = snap.config.budget
        self.max_turns = snap.config.max_turns
        self.max_parallel = snap.config.max_parallel

        # Restore stats
        self.stats = TurnStats(
            budget=snap.config.budget,
            max_turns=snap.config.max_turns,
            total_cost=snap.stats.total_cost,
            turns=snap.stats.turns,
        )

        # Restore exchanges from conversation entries
        from reverser.tui.session import Exchange  # forward import (same module)
        self.exchanges = [
            Exchange(
                user=e.user,
                agent=e.agent,
                turn=e.turn,
                timestamp=e.timestamp,
                cost=e.cost,
            )
            for e in snap.conversation
        ]

        # Reuse the existing log
        self._log_path = snap.log_path
        self._slog = SessionLog(self._log_path)
        # Log the resume event (Task 9 adds log_session_resumed; for now,
        # call log_text or log_session_start with a hint until that lands)
        # Once Task 9 lands:
        #   self._slog.log_session_resumed(snap.session_id, snap.stats.turns,
        #                                  snap.stats.total_cost)

        self._cancel = False
        self._stop_requested = False  # Task 7 adds the stop machinery

        # Take ownership: mark snapshot active with current pid
        snap.state = "active"
        snap.pid = os.getpid()
        self._snapshot = snap
        save_snapshot(self._snapshot)
```

The exact set of fields you copy from `_init_new` depends on what's currently in `__init__`. The tests in Step 1 cover the critical ones (target, budget, max_turns, exchanges, log_path).

If `_WEB_PROFILES` lives elsewhere, import accordingly. The check `profile.key in _WEB_PROFILES` matches the existing `_is_web` logic in `__init__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_resume.py -v`

Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 355 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tui/session.py tests/test_session_resume.py
git commit -m "$(cat <<'EOF'
feat(session): resume_from parameter + __init__ split

Session.__init__ now branches: resume_from=snap restores from a
SessionSnapshot (target, profile validation, budget/turns, stats,
exchanges, log path); resume_from=None constructs a fresh session
(unchanged behavior, plus initial snapshot write).

Both paths set current_session ContextVar so session-aware tools
(dispatch_specialist) can reach the running Session.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `Session.stop()` and `Session.mark_completed()`

**Files:**
- Modify: `src/reverser/tui/session.py`
- Create: `tests/test_session_lifecycle.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session_lifecycle.py`:

```python
"""Tests for the Session lifecycle: active → stopped / completed."""

import os
import pytest
from unittest.mock import MagicMock


def test_stop_transitions_to_stopped_state(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.sessions import load

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    sid = sess._snapshot.session_id
    sess.stop()

    loaded = load("10.10.10.5", sid)
    assert loaded.state == "stopped"
    assert loaded.stopped_at is not None
    assert loaded.pid is None
    # Cancel/stop flags set
    assert sess._cancel is True
    assert sess._stop_requested is True


def test_mark_completed_transitions_to_completed_state(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.sessions import load

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    sid = sess._snapshot.session_id
    sess.mark_completed()

    loaded = load("10.10.10.5", sid)
    assert loaded.state == "completed"
    assert loaded.stopped_at is not None
    assert loaded.pid is None


def test_stop_after_completed_is_noop_on_state(tmp_path, monkeypatch):
    """completed is terminal; stop() shouldn't downgrade it."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.sessions import load

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    sid = sess._snapshot.session_id
    sess.mark_completed()
    sess.stop()  # should not transition completed → stopped

    loaded = load("10.10.10.5", sid)
    assert loaded.state == "completed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_lifecycle.py -v`

Expected: FAIL — `AttributeError: 'Session' object has no attribute 'stop'` (and `mark_completed`).

- [ ] **Step 3: Add `stop()` and `mark_completed()` to `Session`**

In `src/reverser/tui/session.py`:

```python
def stop(self) -> None:
    """User-initiated stop. Marks state stopped and persists.

    Distinct from cancel(): cancel halts a single in-flight query;
    stop signals "I'm done for now, expect to resume." Sets the cancel
    flag too so any in-flight turn unwinds.
    """
    if self._snapshot.state == "completed":
        # Terminal — don't downgrade
        return
    self._cancel = True
    self._stop_requested = True
    self._snapshot.state = "stopped"
    self._snapshot.stopped_at = _now_iso_session()
    self._snapshot.pid = None
    save_snapshot(self._snapshot)
    # Audit log: full implementation comes in Task 9
    try:
        self._slog.log_session_end(
            result="stopped by user",
            cost=self.stats.total_cost,
            turns=self.stats.turns,
            subtype="stopped_by_user",
        )
    except Exception:
        pass  # logging is best-effort; don't block the stop

def mark_completed(self) -> None:
    """Mark session completed (terminal). Won't appear in resume list by default."""
    self._snapshot.state = "completed"
    self._snapshot.stopped_at = _now_iso_session()
    self._snapshot.pid = None
    save_snapshot(self._snapshot)
    try:
        self._slog.log_session_end(
            result="completed by user",
            cost=self.stats.total_cost,
            turns=self.stats.turns,
            subtype="completed_by_user",
        )
    except Exception:
        pass
```

If `_stop_requested` doesn't yet exist as an instance attribute, add `self._stop_requested = False` to both `_init_new` and `_init_resumed`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_lifecycle.py -v`

Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 358 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tui/session.py tests/test_session_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(session): stop() and mark_completed() lifecycle methods

stop() flips state → stopped and clears pid; sets _cancel + _stop_requested
to unwind any in-flight turn. mark_completed() flips state → completed
(terminal). Both persist immediately and append a session_end audit
event.

stop() refuses to downgrade a completed session.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Per-turn autosave hook

**Files:**
- Modify: `src/reverser/tui/session.py`
- Modify: `tests/test_session_lifecycle.py`

- [ ] **Step 1: Write a failing test**

Append to `tests/test_session_lifecycle.py`:

```python
def test_per_turn_autosave_updates_snapshot(tmp_path, monkeypatch):
    """Each completed turn rewrites the snapshot with current stats + exchanges."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session, Exchange
    from reverser.sessions import load

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    sid = sess._snapshot.session_id

    # Simulate two turns by adding exchanges and bumping stats, then calling
    # the autosave method directly.
    sess.exchanges.append(Exchange(
        user="hi", agent="hello", turn=1,
        timestamp="2026-05-09T14:23:00", cost=0.01,
    ))
    sess.stats.total_cost = 0.01
    sess.stats.turns = 1
    sess._autosave_snapshot()

    loaded = load("10.10.10.5", sid)
    assert loaded.stats.turns == 1
    assert loaded.stats.total_cost == 0.01
    assert len(loaded.conversation) == 1
    assert loaded.conversation[0].user == "hi"

    sess.exchanges.append(Exchange(
        user="next", agent="ok", turn=2,
        timestamp="2026-05-09T14:24:00", cost=0.02,
    ))
    sess.stats.total_cost = 0.03
    sess.stats.turns = 2
    sess._autosave_snapshot()

    loaded = load("10.10.10.5", sid)
    assert loaded.stats.turns == 2
    assert loaded.stats.total_cost == 0.03
    assert len(loaded.conversation) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_lifecycle.py::test_per_turn_autosave_updates_snapshot -v`

Expected: FAIL — `AttributeError: 'Session' object has no attribute '_autosave_snapshot'`.

- [ ] **Step 3: Add `_autosave_snapshot` to `Session`**

In `src/reverser/tui/session.py`:

```python
def _autosave_snapshot(self) -> None:
    """Update the snapshot with current stats + exchanges and persist.

    Called at the end of each turn. Cheap (snapshot is a few KB JSON).
    """
    from reverser.sessions import ConversationEntry  # local to avoid early import
    self._snapshot.stats.total_cost = self.stats.total_cost
    self._snapshot.stats.turns = self.stats.turns
    self._snapshot.conversation = [
        ConversationEntry(
            user=e.user, agent=e.agent, turn=e.turn,
            timestamp=e.timestamp, cost=e.cost,
        )
        for e in self.exchanges
    ]
    save_snapshot(self._snapshot)
```

Then wire it into `_run_one_turn` at the very end (after the existing `self.exchanges.append(...)` call from Task 5):

```python
# At the end of _run_one_turn, after appending the new Exchange:
self._autosave_snapshot()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_lifecycle.py -v`

Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 359 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tui/session.py tests/test_session_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(session): per-turn snapshot autosave

After each turn completes, _autosave_snapshot() updates the snapshot
with current stats + full conversation list and persists. Cheap
(snapshot is a few KB JSON) and gives crash recovery for free — the
most-recent completed turn is always durable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: SessionLog new event kinds (`session_resumed` / `session_stopped` / `session_completed`)

**Files:**
- Modify: `src/reverser/session_log.py`
- Modify: `src/reverser/tui/session.py` — call the new log methods from `_init_resumed`/`stop`/`mark_completed`
- Create: `tests/test_session_log_events.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session_log_events.py`:

```python
"""Tests for the new SessionLog event kinds."""

import json
import pytest


def test_log_session_resumed_writes_jsonl_event(tmp_path):
    from reverser.session_log import SessionLog

    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    slog.log_session_resumed(
        session_id="2026-05-09T14-23-00",
        prior_turn=42,
        prior_cost=1.84,
    )
    slog.close()

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["kind"] == "session_resumed"
    assert event["session_id"] == "2026-05-09T14-23-00"
    assert event["prior_turn"] == 42
    assert event["prior_cost"] == 1.84
    assert "timestamp" in event


def test_log_session_stopped_writes_event(tmp_path):
    from reverser.session_log import SessionLog

    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    slog.log_session_stopped(cost=2.50, turns=42)
    slog.close()

    lines = log_path.read_text().strip().split("\n")
    event = json.loads(lines[-1])
    assert event["kind"] == "session_stopped"
    assert event["cost"] == 2.50
    assert event["turns"] == 42


def test_log_session_completed_writes_event(tmp_path):
    from reverser.session_log import SessionLog

    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    slog.log_session_completed(cost=3.75, turns=55)
    slog.close()

    lines = log_path.read_text().strip().split("\n")
    event = json.loads(lines[-1])
    assert event["kind"] == "session_completed"
    assert event["cost"] == 3.75
    assert event["turns"] == 55
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_log_events.py -v`

Expected: FAIL — `AttributeError: 'SessionLog' object has no attribute 'log_session_resumed'`.

- [ ] **Step 3: Add the new methods to `SessionLog`**

In `src/reverser/session_log.py`, add:

```python
def log_session_resumed(self, session_id: str, prior_turn: int, prior_cost: float):
    self._write({
        "kind": "session_resumed",
        "session_id": session_id,
        "prior_turn": prior_turn,
        "prior_cost": prior_cost,
        "timestamp": _now_iso_log(),  # use existing helper or inline
    })

def log_session_stopped(self, cost: float, turns: int):
    self._write({
        "kind": "session_stopped",
        "cost": cost,
        "turns": turns,
        "timestamp": _now_iso_log(),
    })

def log_session_completed(self, cost: float, turns: int):
    self._write({
        "kind": "session_completed",
        "cost": cost,
        "turns": turns,
        "timestamp": _now_iso_log(),
    })
```

If `_now_iso_log` doesn't exist, look for the timestamp pattern used by existing `log_*` methods (likely a module-level helper or inlined `datetime.now`). Match what's there.

- [ ] **Step 4: Update Session calls**

In `src/reverser/tui/session.py`, replace the existing `try: self._slog.log_session_end(...)` calls in `stop()` and `mark_completed()` with the new specific event methods:

```python
# In stop():
try:
    self._slog.log_session_stopped(
        cost=self.stats.total_cost, turns=self.stats.turns,
    )
except Exception:
    pass

# In mark_completed():
try:
    self._slog.log_session_completed(
        cost=self.stats.total_cost, turns=self.stats.turns,
    )
except Exception:
    pass
```

In `_init_resumed`, after `self._slog = SessionLog(...)`:

```python
try:
    self._slog.log_session_resumed(
        session_id=snap.session_id,
        prior_turn=snap.stats.turns,
        prior_cost=snap.stats.total_cost,
    )
except Exception:
    pass
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_session_log_events.py tests/test_session_lifecycle.py tests/test_session_resume.py -v`

Expected: PASS — all event tests + lifecycle tests still pass.

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 362 passed, 1 skipped (359 + 3 new).

- [ ] **Step 7: Commit**

```bash
git add src/reverser/session_log.py src/reverser/tui/session.py tests/test_session_log_events.py
git commit -m "$(cat <<'EOF'
feat(session-log): add session_resumed/stopped/completed event kinds

The audit JSONL log now distinguishes lifecycle transitions from
generic session_end events. log_session_resumed records prior turn/cost
so the audit log of a resumed session has continuity context.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: TUI modals scaffolding

**Files:**
- Create: `src/reverser/tui/modals/__init__.py`
- Create: `src/reverser/tui/modals/stop_confirm.py`
- Create: `src/reverser/tui/modals/done_confirm.py`
- Create: `src/reverser/tui/modals/info.py`

- [ ] **Step 1: Audit existing modal patterns in `src/reverser/tui/app.py`**

Run: `grep -n "ModalScreen\|class.*Modal" src/reverser/tui/app.py | head -10`

Note the existing modal pattern (likely `ProfilePickerModal` or similar). The new modals must follow the same idiom — Textual's `ModalScreen` subclass, CSS for styling, `compose()` method, action methods for buttons.

- [ ] **Step 2: Create the package init**

Create `src/reverser/tui/modals/__init__.py`:

```python
"""TUI modal dialogs for stop/resume operations."""

from .stop_confirm import StopConfirmModal
from .done_confirm import DoneConfirmModal
from .info import SessionInfoModal

__all__ = ["StopConfirmModal", "DoneConfirmModal", "SessionInfoModal"]
```

- [ ] **Step 3: Create `stop_confirm.py`**

```python
"""Modal: confirm stopping the session."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class StopConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation: stop the session and save snapshot."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
    ]

    DEFAULT_CSS = """
    StopConfirmModal {
        align: center middle;
    }

    #stop-dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #stop-dialog Label {
        margin-bottom: 1;
    }

    #stop-buttons {
        height: 3;
        align: center middle;
    }

    #stop-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="stop-dialog"):
            yield Label("Stop session?", classes="modal-title")
            yield Static(
                "Snapshot will be saved as resumable. "
                "You can return to it later with `--resume`."
            )
            with Horizontal(id="stop-buttons"):
                yield Button("Yes (y)", id="yes", variant="primary")
                yield Button("No (n)", id="no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
```

- [ ] **Step 4: Create `done_confirm.py`**

```python
"""Modal: confirm marking the session completed (terminal)."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class DoneConfirmModal(ModalScreen[bool]):
    """Yes/No: mark session completed; terminal — won't appear in resume list."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
    ]

    DEFAULT_CSS = """
    DoneConfirmModal {
        align: center middle;
    }

    #done-dialog {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }

    #done-dialog Label {
        margin-bottom: 1;
    }

    #done-buttons {
        height: 3;
        align: center middle;
    }

    #done-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="done-dialog"):
            yield Label("Mark session completed?", classes="modal-title")
            yield Static(
                "Completed sessions are TERMINAL — they don't appear in the "
                "default resume list. Use this when the engagement is truly done."
            )
            with Horizontal(id="done-buttons"):
                yield Button("Yes (y)", id="yes", variant="warning")
                yield Button("No (n)", id="no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
```

- [ ] **Step 5: Create `info.py`**

```python
"""Modal: read-only display of session metadata."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from reverser.sessions import SessionSnapshot


class SessionInfoModal(ModalScreen[None]):
    """Show session_id, target, profile, started, last_active, cost, turns, state."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    DEFAULT_CSS = """
    SessionInfoModal {
        align: center middle;
    }

    #info-dialog {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #info-dialog Static {
        margin-bottom: 0;
    }

    #info-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, snapshot: SessionSnapshot, total_cost: float, turns: int):
        super().__init__()
        self._snap = snapshot
        self._total_cost = total_cost
        self._turns = turns

    def compose(self) -> ComposeResult:
        s = self._snap
        with Vertical(id="info-dialog"):
            yield Label("Session info", classes="modal-title")
            yield Static(f"[bold]Session ID:[/]  {s.session_id}")
            yield Static(f"[bold]Target:[/]      {s.target}")
            yield Static(f"[bold]Profile:[/]     {s.config.profile}")
            yield Static(f"[bold]State:[/]       {s.state}")
            yield Static(f"[bold]Started:[/]     {s.started_at}")
            yield Static(f"[bold]Last active:[/] {s.last_active_at}")
            yield Static(
                f"[bold]Cost:[/]        ${self._total_cost:.4f} / ${s.config.budget:.2f}"
            )
            yield Static(
                f"[bold]Turns:[/]       {self._turns} / {s.config.max_turns}"
            )
            yield Static(f"[bold]Log:[/]         {s.log_path}")
            with Vertical(id="info-buttons"):
                yield Button("Close (q)", id="close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)
```

- [ ] **Step 6: Verify modules import cleanly**

Run:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tui.modals import StopConfirmModal, DoneConfirmModal, SessionInfoModal
print('OK')
"
```

Expected: `OK`. If a Textual API import fails, adjust the imports to match the installed Textual version.

- [ ] **Step 7: Run the full suite (should be unaffected)**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 362 passed, 1 skipped (no change — modals are not yet used).

- [ ] **Step 8: Commit**

```bash
git add src/reverser/tui/modals/
git commit -m "$(cat <<'EOF'
feat(tui): add stop/done/info modal dialogs

Three Textual ModalScreens for session lifecycle UX:
- StopConfirmModal: confirm stopping (returns True/False)
- DoneConfirmModal: confirm marking completed (returns True/False)
- SessionInfoModal: read-only display of session metadata

Modals are not wired into the app yet; that comes in Task 11.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: TUI keybindings + slash command interceptor

**Files:**
- Modify: `src/reverser/tui/app.py`

- [ ] **Step 1: Audit current TUI bindings**

Run: `grep -n "BINDINGS\|Binding\|action_" src/reverser/tui/app.py | head -25`

Note the existing pattern (`f1`/`f2` bindings are likely already there per the manager profile work). New bindings join the same `BINDINGS` list on the App class.

- [ ] **Step 2: Add bindings and action methods**

In `src/reverser/tui/app.py`, find the `BINDINGS` list on the main `App` subclass and add:

```python
BINDINGS = [
    # ... existing bindings (f1, f2, etc.) ...
    Binding("f3", "stop_session", "Stop", show=True),
    Binding("f4", "adjust_budget", "Budget", show=True),  # reserved for Feature A
    Binding("f5", "show_session_info", "Info", show=True),
]
```

Add the action methods on the App class:

```python
async def action_stop_session(self) -> None:
    """f3 / /stop — confirm and stop the session."""
    from reverser.tui.modals import StopConfirmModal

    if self.session is None:
        return

    def _on_stop_confirm(confirmed: bool | None) -> None:
        if confirmed:
            self.session.stop()
            self.exit()

    self.push_screen(StopConfirmModal(), _on_stop_confirm)

async def action_adjust_budget(self) -> None:
    """f4 — reserved for live-budget-adjustment (Feature A); no-op for now."""
    self.notify("Budget adjustment not yet available (Feature A pending).",
                severity="information")

async def action_show_session_info(self) -> None:
    """f5 / /info — show read-only session metadata."""
    from reverser.tui.modals import SessionInfoModal

    if self.session is None:
        return

    self.push_screen(SessionInfoModal(
        snapshot=self.session._snapshot,
        total_cost=self.session.stats.total_cost,
        turns=self.session.stats.turns,
    ))
```

- [ ] **Step 3: Add the slash-command interceptor**

Find the input-handler method (likely `on_input_submitted` or similar — the method that processes user text input). Before the existing logic that sends the input to the agent, add:

```python
async def on_input_submitted(self, event: ...) -> None:
    text = event.value.strip()

    # Slash command interception
    if text.startswith("/"):
        cmd = text.lstrip("/").lower()
        if cmd == "stop":
            await self.action_stop_session()
            return
        elif cmd == "done":
            await self._handle_done_command()
            return
        elif cmd == "info":
            await self.action_show_session_info()
            return
        elif cmd == "help":
            self._show_slash_help()
            return
        else:
            self.notify(f"Unknown slash command: /{cmd}. Try /help.",
                        severity="warning")
            return

    # ... existing agent-input handling continues ...

async def _handle_done_command(self) -> None:
    from reverser.tui.modals import DoneConfirmModal

    if self.session is None:
        return

    def _on_done_confirm(confirmed: bool | None) -> None:
        if confirmed:
            self.session.mark_completed()
            self.exit()

    self.push_screen(DoneConfirmModal(), _on_done_confirm)

def _show_slash_help(self) -> None:
    """Display slash command help in the chat panel or as a notification."""
    help_text = (
        "Slash commands:\n"
        "  /stop  — stop session, save snapshot, exit\n"
        "  /done  — mark session completed (terminal), exit\n"
        "  /info  — show session metadata\n"
        "  /help  — this help\n"
        "Keybindings:\n"
        "  f3 — stop\n"
        "  f4 — budget (reserved)\n"
        "  f5 — info"
    )
    self.notify(help_text, timeout=10)
```

The exact event class for `on_input_submitted` depends on which Textual widget handles input (probably `Input.Submitted`). Adjust accordingly. Look at the existing handler signature.

- [ ] **Step 4: Manual smoke check (no automated test for keybindings — they need a TUI)**

Run:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tui.app import ReverserApp  # or whatever the App class is named
print('App imports OK')
print('BINDINGS:', [b.key for b in ReverserApp.BINDINGS])
"
```

Expected: includes `f3`, `f4`, `f5` along with existing bindings.

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 362 passed, 1 skipped (no test changes; we're adding interactive UI surface without modifying test-covered behavior).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tui/app.py
git commit -m "$(cat <<'EOF'
feat(tui): wire f3/f4/f5 keybindings + /stop /done /info /help slash commands

f3/stop opens StopConfirmModal then calls Session.stop() and exits.
f5/info opens SessionInfoModal (read-only metadata display).
f4 is reserved for Feature A (live budget adjustment); shows a notification.
/done opens DoneConfirmModal then calls Session.mark_completed() and exits.
/help lists all slash commands and keybindings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: atexit + SIGTERM emergency snapshot

**Files:**
- Modify: `src/reverser/tui/app.py`
- Create: `tests/test_emergency_snapshot.py`

The autosave per turn is the primary safety net. This is the last-resort capture for the case where the user kills the TUI between turns.

- [ ] **Step 1: Write a failing test**

Create `tests/test_emergency_snapshot.py`:

```python
"""Tests for the atexit / SIGTERM emergency snapshot hook."""

import os
from unittest.mock import MagicMock


def test_emergency_snapshot_writes_when_session_present(tmp_path, monkeypatch):
    """The emergency_snapshot helper writes the current session's snapshot."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.tui.app import _emergency_snapshot
    from reverser.sessions import load

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("general"),
        backend=MagicMock(),
    )
    sid = sess._snapshot.session_id
    sess.stats.turns = 7
    sess.stats.total_cost = 0.42

    _emergency_snapshot(sess)

    loaded = load("10.10.10.5", sid)
    assert loaded.stats.turns == 7
    assert loaded.stats.total_cost == 0.42


def test_emergency_snapshot_handles_none_session(tmp_path, monkeypatch):
    """Called with None session, _emergency_snapshot is a no-op (no exception)."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.tui.app import _emergency_snapshot

    _emergency_snapshot(None)  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_emergency_snapshot.py -v`

Expected: FAIL — `ImportError: cannot import name '_emergency_snapshot'`.

- [ ] **Step 3: Add the helper and register hooks**

In `src/reverser/tui/app.py`, near the top of the module:

```python
import atexit
import signal


def _emergency_snapshot(session) -> None:
    """Best-effort save on interpreter shutdown — runs even on crash/SIGTERM.

    Called from atexit and SIGTERM signal handler. Catches all exceptions
    because we're shutting down; nothing useful we can do if save fails.
    """
    if session is None:
        return
    try:
        from reverser.sessions import save as save_snapshot
        # Update last_active_at, then save. Don't update stats/conversation —
        # those are only authoritative on the Session, and we may be in a
        # partial state mid-turn.
        save_snapshot(session._snapshot)
    except Exception:
        pass  # we're shutting down


def _register_emergency_hooks(app) -> None:
    """Wire atexit + SIGTERM to emergency_snapshot of the app's current session.

    Idempotent — safe to call multiple times.
    """
    atexit.register(lambda: _emergency_snapshot(getattr(app, "session", None)))

    def _sigterm_handler(*_args):
        _emergency_snapshot(getattr(app, "session", None))
        # Re-raise default behavior
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    signal.signal(signal.SIGTERM, _sigterm_handler)
```

In the App class's `on_mount` or `__init__`, call `_register_emergency_hooks(self)` after the session is constructed:

```python
def on_mount(self) -> None:
    # ... existing setup ...
    _register_emergency_hooks(self)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_emergency_snapshot.py -v`

Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 364 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tui/app.py tests/test_emergency_snapshot.py
git commit -m "$(cat <<'EOF'
feat(tui): atexit + SIGTERM emergency snapshot

Last-resort snapshot save on interpreter shutdown. The per-turn autosave
is the primary safety net; this catches the case where the user kills
the TUI between turns. Best-effort — exceptions during shutdown are
swallowed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Header + chat-pane resume rendering

**Files:**
- Modify: `src/reverser/tui/app.py`

- [ ] **Step 1: Update the header rendering**

Find where the chat panel's header is rendered (likely a `Static` or `Label` widget set during `compose()` or `on_mount()`). Update to include session info:

```python
def _render_header(self) -> str:
    """Build the chat panel header string."""
    if self.session is None:
        return f"Reverser Agent — Profile: {self.profile.name}"

    snap = self.session._snapshot
    cost_str = f"${self.session.stats.total_cost:.2f}/${snap.config.budget:.2f}"
    turns_str = f"{self.session.stats.turns}/{snap.config.max_turns} turns"
    resumed_marker = " (resumed)" if hasattr(self, "_was_resumed") and self._was_resumed else ""

    return (
        f"Reverser Agent — Profile: {self.profile.name}  "
        f"Session: {snap.session_id}{resumed_marker}, {turns_str}, {cost_str}"
    )
```

Wherever the header is set, call `self._render_header()`. If the header is computed once at startup, also recompute it after stats update (e.g. after each turn) so cost/turns stay current.

- [ ] **Step 2: Add resume-time chat-pane replay**

In the App's `on_mount` (after the Session is constructed), add:

```python
def on_mount(self) -> None:
    # ... existing setup including session construction ...
    _register_emergency_hooks(self)

    # If this is a resumed session, replay conversation into the chat pane
    if self._was_resumed and self.session is not None:
        self._replay_conversation_pane()

def _replay_conversation_pane(self) -> None:
    """Re-render the chat pane from the session's conversation history."""
    chat_panel = self.query_one("#chat-panel")  # or whatever the chat panel ID is
    snap = self.session._snapshot

    # Show progress for long sessions
    n = len(snap.conversation)
    if n > 50:
        self.notify(f"Replaying conversation: {n} entries...", timeout=3)

    for entry in snap.conversation:
        chat_panel.write(f"[bold]You ({entry.timestamp})[/]: {entry.user}")
        chat_panel.write(f"[bold]Agent[/]: {entry.agent}")
        chat_panel.write("")

    # Show in_flight notice if applicable
    if snap.in_flight is not None:
        chat_panel.write(
            f"[bold yellow]⚠ Previous session was stopped during dispatch "
            f"to '{snap.in_flight.specialty}' (hypothesis #{snap.in_flight.hypothesis_id}). "
            f"Hypothesis status is still 'testing'.[/]"
        )
        chat_panel.write("")
```

The exact widget query (`#chat-panel`, `chat_panel.write(...)`) depends on the existing TUI structure. Inspect the existing `compose()` method to find the right widget ID and method.

The `_was_resumed` attribute is set by the App constructor or `on_mount`:

```python
def __init__(self, *args, was_resumed: bool = False, **kwargs):
    super().__init__(*args, **kwargs)
    self._was_resumed = was_resumed
```

When the CLI launches the TUI with a resumed session (Task 15), it passes `was_resumed=True`.

- [ ] **Step 3: Update the header on each turn**

Find where `AgentEvent` events are handled (likely in `on_session_event` or similar). After processing an event that updates stats (`turn` or `result`), recompute the header:

```python
async def on_session_event(self, event: AgentEvent) -> None:
    # ... existing handling ...

    if event.kind in ("turn", "result"):
        # Refresh the header to show current cost/turns
        header_widget = self.query_one("#chat-header")  # or whatever ID
        header_widget.update(self._render_header())
```

Adjust widget IDs to match the existing structure.

- [ ] **Step 4: Verify TUI imports cleanly (no automated test for header rendering)**

Run:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tui.app import ReverserApp
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 364 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tui/app.py
git commit -m "$(cat <<'EOF'
feat(tui): header shows session info + chat-pane replays on resume

Header line now includes session_id, resumed marker, turn count, and
cost vs budget — refreshed on each turn boundary. On resume, the chat
pane replays the snapshot's conversation history before the input
becomes active. If the snapshot has in_flight set, a yellow warning
note tells the operator about the abandoned dispatch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Top-level `--list-sessions` flag

**Files:**
- Modify: `src/reverser/cli.py`
- Create: `tests/test_cli_sessions.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli_sessions.py`:

```python
"""Tests for the --list-sessions CLI flag."""

import subprocess
import os

PYTHON = "/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python"


def _run_cli(args: list[str], env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [PYTHON, "-m", "reverser", *args],
        capture_output=True, text=True, env=env,
    )


def test_list_sessions_with_no_sessions_says_empty(tmp_path):
    """--list-sessions on an empty targets dir says 'no sessions'."""
    result = _run_cli(
        ["--list-sessions"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "no session" in result.stdout.lower() or len(result.stdout.strip()) > 0


def test_list_sessions_shows_existing_sessions(tmp_path):
    """--list-sessions shows the session table when sessions exist."""
    # Pre-populate a snapshot file
    sessions_dir = tmp_path / "10.10.10.5" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "2026-05-09T14-23-00.json").write_text("""
{
  "session_id": "2026-05-09T14-23-00",
  "target": "10.10.10.5",
  "log_path": "logs/test.jsonl",
  "state": "stopped",
  "started_at": "2026-05-09T14:23:00",
  "last_active_at": "2026-05-09T18:47:00",
  "config": {"profile": "manager", "budget": 5.0, "max_turns": 50, "max_parallel": 1, "backend": "claude", "model": null, "api_base": null},
  "stats": {"total_cost": 1.84, "turns": 47},
  "conversation": [],
  "ui": {"focused_panel": "chat", "chat_scroll_position": 0, "last_skill_key": null, "input_buffer": ""},
  "schema_version": 1
}
""")

    result = _run_cli(
        ["--list-sessions"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "2026-05-09T14-23-00" in result.stdout
    assert "10.10.10.5" in result.stdout
    assert "manager" in result.stdout
    assert "stopped" in result.stdout
    # Cost displayed somewhere
    assert "1.84" in result.stdout or "$1.84" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_cli_sessions.py -v`

Expected: FAIL — `--list-sessions` not recognized as a top-level argument.

- [ ] **Step 3: Add `--list-sessions` to the top-level parser**

In `src/reverser/cli.py`, find the top-level `parser` (the one parented to the subparsers, not on any specific subparser):

```python
parser.add_argument(
    "--list-sessions",
    action="store_true",
    help="List all resumable sessions across targets and exit.",
)
```

Add a handler near the top of `main()` (after `args = parser.parse_args(...)` but before the subcommand dispatch):

```python
if args.list_sessions:
    from reverser.sessions import list_all
    snapshots = list_all()  # default: include completed
    if not snapshots:
        print("No sessions found.")
        return 0

    print("Sessions across all targets:")
    print(f"  {'TARGET':<16} {'ID':<24} {'STATE':<11} {'PROFILE':<10} "
          f"{'STARTED':<20} {'LAST ACTIVE':<20} {'TURNS':>6} {'COST':>8}")
    for s in snapshots:
        cost_str = f"${s.stats.total_cost:.2f}"
        print(
            f"  {s.target:<16} {s.session_id:<24} {s.state:<11} "
            f"{s.config.profile:<10} {s.started_at:<20} "
            f"{s.last_active_at:<20} {s.stats.turns:>6} {cost_str:>8}"
        )
    print()
    print("Resume the latest session for a target with: reverser i <target> --resume")
    print("Resume a specific session with:              reverser i --resume <ID>")
    return 0
```

If `main()` doesn't currently use `return` codes, adjust to match the existing style (sometimes it just exits via `sys.exit()` or returns nothing).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_cli_sessions.py -v`

Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 366 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/cli.py tests/test_cli_sessions.py
git commit -m "$(cat <<'EOF'
feat(cli): add top-level --list-sessions flag

Walks targets/*/sessions/ and prints a table of all snapshots (across
all targets) sorted by last_active_at desc. Shows target, session_id,
state, profile, started_at, last_active_at, turns, cost.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `--resume [SESSION_ID]` on `interactive` + resume routing

**Files:**
- Modify: `src/reverser/cli.py`
- Modify: `tests/test_cli_sessions.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli_sessions.py`:

```python
def test_resume_help_mentions_resume_flag():
    """The interactive --help output mentions --resume."""
    result = _run_cli(["interactive", "--help"])
    assert "--resume" in result.stdout, result.stdout


def test_resume_with_unknown_session_id_errors(tmp_path):
    """--resume <unknown-id> exits with an error."""
    result = _run_cli(
        ["interactive", "10.10.10.5", "--resume", "nonexistent-session"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "no snapshot" in result.stderr.lower()


def test_resume_with_completed_session_errors(tmp_path):
    """--resume against a completed session is refused."""
    sessions_dir = tmp_path / "10.10.10.5" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "completed-1.json").write_text("""
{
  "session_id": "completed-1",
  "target": "10.10.10.5",
  "log_path": "logs/test.jsonl",
  "state": "completed",
  "started_at": "2026-05-09T14:23:00",
  "last_active_at": "2026-05-09T18:47:00",
  "config": {"profile": "general", "budget": 5.0, "max_turns": 50, "max_parallel": 1, "backend": "claude", "model": null, "api_base": null},
  "stats": {"total_cost": 1.84, "turns": 47},
  "conversation": [],
  "ui": {"focused_panel": "chat", "chat_scroll_position": 0, "last_skill_key": null, "input_buffer": ""},
  "schema_version": 1
}
""")

    result = _run_cli(
        ["interactive", "10.10.10.5", "--resume", "completed-1"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "completed" in result.stderr.lower()


def test_resume_no_target_no_sessions_errors(tmp_path):
    """--resume without target arg and no sessions anywhere errors clearly."""
    result = _run_cli(
        ["interactive", "--resume"],
        env_overrides={"REVERSER_TARGETS_DIR": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "no session" in result.stderr.lower() or "no resumable" in result.stderr.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_cli_sessions.py -v`

Expected: 4 new tests fail (`--resume` not recognized).

- [ ] **Step 3: Add `--resume` to the interactive subparser**

In `src/reverser/cli.py`:

```python
interactive_parser.add_argument(
    "--resume",
    nargs="?",
    const="__latest__",  # sentinel: --resume with no ID
    default=None,
    metavar="SESSION_ID",
    help="Resume a session. With no SESSION_ID, resumes the latest "
         "(target-scoped if a target arg is given, else global).",
)
interactive_parser.add_argument(
    "--force",
    action="store_true",
    help="With --resume: take over a session whose process is still running.",
)
```

In the interactive command handler, before the existing Session construction:

```python
def _resolve_resume(args) -> SessionSnapshot | None:
    """Map --resume value + target arg into a SessionSnapshot or raise."""
    from reverser.sessions import (
        load, latest_for_target, latest_global,
        SessionNotFoundError, SessionStateError, is_session_alive,
    )

    if args.resume is None:
        return None  # not resuming

    target = args.target if args.target else None

    if args.resume == "__latest__":
        # No specific ID → look up latest
        if target:
            snap = latest_for_target(target, exclude_completed=True)
            if snap is None:
                raise SessionStateError(
                    f"No resumable sessions for {target}. "
                    f"Start a new session with: reverser i -p <profile> {target}"
                )
        else:
            snap = latest_global(exclude_completed=True)
            if snap is None:
                raise SessionStateError(
                    "No sessions to resume. "
                    "Start with: reverser i -p <profile> <target>"
                )
    else:
        # Specific ID → look up
        if not target:
            # Need to find which target this ID belongs to
            from reverser.sessions import list_all
            for s in list_all():
                if s.session_id == args.resume:
                    target = s.target
                    break
            if not target:
                raise SessionNotFoundError(
                    f"No snapshot with session_id={args.resume!r} found. "
                    f"Run reverser --list-sessions to see available sessions."
                )
        try:
            snap = load(target, args.resume)
        except SessionNotFoundError as e:
            raise SessionStateError(str(e))

    # Reject completed
    if snap.state == "completed":
        raise SessionStateError(
            f"Session {snap.session_id} is completed and cannot be resumed. "
            f"Run reverser --list-sessions to see other options."
        )

    # Liveness check
    if is_session_alive(snap) and not args.force:
        raise SessionStateError(
            f"Session {snap.session_id} is currently running in PID {snap.pid}. "
            f"Use --force to take over (warning: the original process's "
            f"writes will conflict)."
        )

    # Profile match check (only if -p was explicitly passed)
    # The argparse default for --profile is "general"; we can't tell if
    # the user explicitly set it. Use a sentinel approach: check if -p
    # was in sys.argv.
    import sys
    profile_explicit = any(arg in ("-p", "--profile") for arg in sys.argv[1:])
    if profile_explicit and args.profile != snap.config.profile:
        raise SessionStateError(
            f"Resume must use the same profile (snapshot uses "
            f"{snap.config.profile!r}; got -p {args.profile!r}). "
            f"Drop -p to use the snapshot's profile, or start a new session."
        )

    return snap
```

In the interactive command body, where the Session is currently constructed:

```python
import sys

resume_snap = None
try:
    resume_snap = _resolve_resume(args)
except (SessionNotFoundError, SessionStateError) as e:
    print(f"Error: {e}", file=sys.stderr)
    return 1

if resume_snap is not None:
    # Override args from snapshot (CLI flags only win if explicitly set)
    target = resume_snap.target
    profile = get_profile(resume_snap.config.profile)
    # If --budget / --max-turns weren't explicitly set, use snapshot values
    budget_explicit = any(arg in ("--budget",) for arg in sys.argv[1:])
    max_turns_explicit = any(arg in ("--max-turns",) for arg in sys.argv[1:])
    budget = args.budget if budget_explicit else resume_snap.config.budget
    max_turns = args.max_turns if max_turns_explicit else resume_snap.config.max_turns
else:
    target = args.target
    profile = get_profile(args.profile)
    budget = args.budget
    max_turns = args.max_turns

# Then construct the App with resume_snap=resume_snap (and TUI shows resumed indicator)
# ... existing TUI launch with the additional resume_from=resume_snap parameter ...
```

The exact TUI launch code already exists; you're adding `resume_from=resume_snap` (and `was_resumed=resume_snap is not None` for the App's UI hint).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_cli_sessions.py -v`

Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 370 passed, 1 skipped (366 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/cli.py tests/test_cli_sessions.py
git commit -m "$(cat <<'EOF'
feat(cli): --resume [SESSION_ID] on interactive subcommand

--resume with no ID resumes the latest session (target-scoped if a
target arg is given, else global). --resume <ID> resumes a specific
session by ID. --force overrides liveness check; completed sessions
are never resumable. Profile/budget/max_turns from snapshot win unless
explicitly overridden on the CLI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: dispatch_specialist in_flight + cancel grace (CONDITIONAL)

**Files:**
- Modify: `src/reverser/tools/dispatch.py` (only if it exists)
- Create: `tests/test_dispatch_in_flight.py` (only if dispatch.py exists)

**SKIP THIS TASK if `src/reverser/tools/dispatch.py` doesn't exist.** That file is added by the manager profile work (separate plan); if you're working on `main` before manager-profile is merged, this task has nothing to modify. Re-run this task after the merge.

- [ ] **Step 1: Check whether the file exists**

Run: `test -f src/reverser/tools/dispatch.py && echo present || echo absent`

If `absent`: skip the rest of this task and move on to Task 17.

- [ ] **Step 2: Write failing tests**

Create `tests/test_dispatch_in_flight.py`:

```python
"""Tests for dispatch_specialist in_flight tracking + cancel grace."""

import asyncio
from unittest.mock import patch, MagicMock


def _call_tool(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def test_dispatch_sets_in_flight_on_session_snapshot(monkeypatch, tmp_path):
    """When dispatch_specialist starts, it mutates session._snapshot.in_flight."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import Session
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = Session(
        target="10.10.10.5",
        profile=get_profile("manager") if "manager" in [p.key for p in __import__("reverser.profiles", fromlist=["list_profiles"]).list_profiles()] else get_profile("general"),
        backend=MagicMock(),
    )
    current_session.set(sess)

    # Capture the snapshot's in_flight at the moment query() is called
    captured_in_flight_during_call = []

    async def capturing_query(prompt, options):
        captured_in_flight_during_call.append(sess._snapshot.in_flight)
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")], model="claude")
        yield ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="test",
            total_cost_usd=0.0, result="x",
        )

    with patch("reverser.tools.dispatch.query", capturing_query):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "test",
            "target": "10.10.10.5",
            "hypothesis_id": 1,
        })

    # During the SDK call, in_flight should have been set
    assert captured_in_flight_during_call[0] is not None
    assert captured_in_flight_during_call[0].specialty == "ad"
    assert captured_in_flight_during_call[0].sub_goal == "test"
    # After dispatch returns, in_flight is cleared
    assert sess._snapshot.in_flight is None


def test_dispatch_in_flight_cleared_on_error(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile, list_profiles
    from reverser.tui.session import Session
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    profile = get_profile("manager") if "manager" in [p.key for p in list_profiles()] else get_profile("general")
    sess = Session(target="10.10.10.5", profile=profile, backend=MagicMock())
    current_session.set(sess)

    async def fail_query(prompt, options):
        raise RuntimeError("boom")
        yield  # makes it an async generator

    with patch("reverser.tools.dispatch.query", fail_query):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "test",
            "target": "10.10.10.5",
        })

    # in_flight cleared even after error
    assert sess._snapshot.in_flight is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_dispatch_in_flight.py -v`

Expected: FAIL — `dispatch_specialist` doesn't currently set `in_flight`.

- [ ] **Step 4: Modify `src/reverser/tools/dispatch.py`**

Find the `dispatch_specialist` function. Around the SDK `query()` call, add:

```python
# At the top of the function (after argument extraction):
from reverser.sessions import current_session, InFlightDispatch, save as save_snapshot
sess = current_session.get()

# Just BEFORE the `try: async for message in query(...):` block:
if sess is not None:
    sess._snapshot.in_flight = InFlightDispatch(
        kind="dispatch",
        specialty=specialty,
        hypothesis_id=hypothesis_id,
        sub_goal=sub_goal,
        started_at=_now_iso_dispatch(),  # or inline datetime.now
    )
    try:
        save_snapshot(sess._snapshot)
    except Exception:
        pass

# In a `finally:` block at the end of the dispatch (after the try/except):
finally:
    if sess is not None:
        sess._snapshot.in_flight = None
        try:
            save_snapshot(sess._snapshot)
        except Exception:
            pass
```

If `_now_iso_dispatch` doesn't exist, add a small helper or inline `datetime.now(timezone.utc).isoformat(timespec="seconds")`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_dispatch_in_flight.py -v`

Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 372 passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch_in_flight.py
git commit -m "$(cat <<'EOF'
feat(dispatch): set in_flight on snapshot during dispatch

dispatch_specialist now reads current_session ContextVar and mutates
session._snapshot.in_flight before the SDK query() call, then clears
it (in a finally block) after. Resume tooling can detect that a
previous session was stopped mid-dispatch and surface the situation
to the manager.

5-second cancel grace (the spec mentions) requires async signal
plumbing that's deferred to a follow-up — for v1 we rely on the
existing cancel mechanism in the parent Session.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Manager profile SKILL_WRAPUP prompt update (CONDITIONAL)

**Files:**
- Modify: `src/reverser/profiles/manager.py` (only if it exists)

**SKIP THIS TASK if `src/reverser/profiles/manager.py` doesn't exist.**

- [ ] **Step 1: Check whether the file exists**

Run: `test -f src/reverser/profiles/manager.py && echo present || echo absent`

If `absent`: skip the rest of this task.

- [ ] **Step 2: Modify the SKILL_WRAPUP prompt**

Open `src/reverser/profiles/manager.py`. Find `SKILL_WRAPUP`. Append a sentence to the prompt:

```python
SKILL_WRAPUP = Skill(
    name="Wrap up",
    key="w",
    description="Mark unresolved hypotheses, generate report, stop",
    prompt=(
        "Engagement is ending. For every hypothesis still in 'proposed' or "
        "'testing' status: mark it as 'abandoned' with a one-line reason "
        "(out of time, out of scope, blocked, etc.). Then generate the "
        "final engagement report (kb_export_report + executive summary). "
        "Finally, print a brief wrap-up message stating the engagement is "
        "complete and where the report was written. "
        "Then tell the user: 'Type /done to mark this session completed and exit.'"
    ),
)
```

- [ ] **Step 3: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 372 passed, 1 skipped (no test changes needed; this is a prompt content update).

- [ ] **Step 4: Commit**

```bash
git add src/reverser/profiles/manager.py
git commit -m "$(cat <<'EOF'
feat(manager): SKILL_WRAPUP prompts user to type /done

After the wrap-up report is generated, the manager now tells the user
to type the /done slash command to mark the session completed and
exit. This explicit-signal approach matches the stop/resume design
spec — the agent doesn't auto-complete; the user signals when truly
done so the session lifecycle is operator-controlled.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Manual smoke test doc

**Files:**
- Create: `tests/manual/resume_smoke.md`

- [ ] **Step 1: Create the doc**

```markdown
# Stop & resume smoke test

A 20-minute walkthrough of the stop/resume feature against any reverser
profile. Use the manager profile if you've got the manager work merged;
otherwise use `general` or `pentest` for a simpler test.

## Preconditions

- `devenv shell` is active
- `REVERSER_PENTEST_AUTHORIZED=1` exported (if using a pentest profile)
- A scratch target — any IP or hostname; doesn't need to be reachable
  for the lifecycle tests

## Steps

### 1. Start a session and verify snapshot creation

```sh
reverser i -p general 10.10.10.5
```

Send a couple of messages. After each turn, in another terminal:

```sh
ls targets/10.10.10.5/sessions/
cat targets/10.10.10.5/sessions/*.json | head -50
```

**Expected:** A `<session_id>.json` file exists. The `state` is `active`,
`pid` matches the running TUI process, `stats.turns` increments per turn,
`conversation` array grows.

### 2. Stop via f3 + verify state transition

In the TUI, press `f3`. Confirm "Yes" in the modal.

**Expected:** TUI exits cleanly. The snapshot file now shows `state: "stopped"`,
`stopped_at: "<timestamp>"`, `pid: null`.

### 3. Resume + verify chat replay

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** TUI starts, the chat pane re-renders all prior exchanges (with
"You" / "Agent" labels), header shows `(resumed)` marker and current cost/turns.
Send a new message.

In another terminal, verify the snapshot updated:

```sh
sqlite3 targets/10.10.10.5/sessions/*.json  # ← actually JSON, just for inspection
# (or use jq):
jq '.stats, .state' targets/10.10.10.5/sessions/*.json
```

**Expected:** `state` is `active` again, `stats.turns` continues from where you
left off (not reset).

### 4. Test /info slash command

In the TUI, type `/info`. **Expected:** modal pops up showing session metadata.
Press `q` or escape to close.

### 5. Test /help

Type `/help`. **Expected:** notification or chat-pane note listing slash
commands and keybindings.

### 6. Crash simulation

Start fresh:

```sh
rm -rf targets/10.10.10.5/sessions/
reverser i -p general 10.10.10.5
```

Send a message. While the TUI is running, find the PID and kill it:

```sh
ps | grep reverser
kill -9 <pid>
```

**Expected:** TUI dies. Snapshot file still exists; `state` is still `active`,
`pid` is set to the now-dead PID.

Resume:

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** Liveness check sees the dead PID, treats the session as resumable,
flips state back to `active` with the new PID. Chat pane re-renders the partial
conversation. New messages continue building on it.

### 7. List sessions

```sh
reverser --list-sessions
```

**Expected:** Table listing the session(s) you've created. Stopped sessions
are shown alongside active ones.

### 8. Mark completed via /done

In a running TUI, type `/done`. Confirm "Yes".

**Expected:** TUI exits cleanly. Snapshot now `state: "completed"`.

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** Errors with "Session ... is completed and cannot be resumed."

```sh
reverser --list-sessions
```

**Expected:** The completed session still appears in the listing (we don't
hide completed by default).

### 9. Force-take-over

Start a session:

```sh
reverser i -p general 10.10.10.5
```

In another terminal, while the first is still running:

```sh
reverser i 10.10.10.5 --resume
```

**Expected:** Errors with "Session ... is currently running in PID X."

```sh
reverser i 10.10.10.5 --resume --force
```

**Expected:** Takes over, with a warning that the original process's writes
will conflict.

## Success criteria

- All 9 steps complete without errors that aren't expected
- Snapshot files persist correctly across stop/resume/crash cycles
- `--list-sessions` shows accurate state
- `/done` is terminal — completed sessions are not resumable

## Cleanup

```sh
rm -rf targets/10.10.10.5/
rm -rf logs/10.10.10.5_*
```
```

- [ ] **Step 2: Commit**

```bash
git add tests/manual/resume_smoke.md
git commit -m "$(cat <<'EOF'
docs(test): add manual smoke test for stop & resume

20-minute walkthrough covering: snapshot creation per turn, stop via
f3, resume + chat replay, /info, /help, crash simulation, list-sessions,
/done (terminal), --force takeover.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: Final integration validation

**Files:**
- Read-only verification

- [ ] **Step 1: Run the full test suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q 2>&1 | tail -5`

Expected: 372 passed (or 370 if Tasks 16/17 were skipped), 1 skipped. Note the exact count.

- [ ] **Step 2: Verify the sessions module API surface**

Run:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.sessions import (
    SessionSnapshot, SessionConfig, SessionStats, ConversationEntry,
    UIState, InFlightDispatch, SessionState,
    snapshot_path, save, load,
    list_for_target, list_all, latest_for_target, latest_global,
    is_session_alive, make_session_id, new_snapshot,
    current_session,
    SessionNotFoundError, SessionStateError, SchemaError,
)
print('sessions module API is complete')
"
```

Expected: prints the message; no ImportError.

- [ ] **Step 3: Verify CLI surface**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -m reverser --help 2>&1 | grep -E "list-sessions|--resume" | head -5`

Expected: at minimum `--list-sessions` appears in the top-level help output.

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -m reverser interactive --help 2>&1 | grep -E "resume|force" | head -5`

Expected: `--resume` and `--force` appear in the interactive subcommand's help.

- [ ] **Step 4: Verify Session class surface**

```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tui.session import Session, Exchange
import inspect
sig = inspect.signature(Session.__init__)
assert 'resume_from' in sig.parameters, 'Session.__init__ missing resume_from'
assert hasattr(Session, 'stop'), 'Session.stop missing'
assert hasattr(Session, 'mark_completed'), 'Session.mark_completed missing'
assert hasattr(Session, '_autosave_snapshot'), 'Session._autosave_snapshot missing'
print('Session class surface complete')
"
```

Expected: prints the message.

- [ ] **Step 5: Verify TUI modals**

```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tui.modals import StopConfirmModal, DoneConfirmModal, SessionInfoModal
print('TUI modals importable')
"
```

Expected: prints the message.

- [ ] **Step 6: End-to-end snapshot smoke check (no TUI)**

```bash
mkdir -p /tmp/reverser-stop-resume-test
REVERSER_TARGETS_DIR=/tmp/reverser-stop-resume-test \
  /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.sessions import (
    new_snapshot, save, load, list_for_target, latest_for_target, SessionConfig,
)

# Create
snap = new_snapshot(
    target='10.10.10.5',
    log_path='logs/test.jsonl',
    config=SessionConfig(profile='general', budget=5.0, max_turns=50),
)
save(snap)
print(f'created session_id={snap.session_id}')

# List
snaps = list_for_target('10.10.10.5')
assert len(snaps) == 1, f'expected 1 snapshot, got {len(snaps)}'

# Latest
latest = latest_for_target('10.10.10.5')
assert latest is not None
assert latest.session_id == snap.session_id

# Round-trip
loaded = load('10.10.10.5', snap.session_id)
assert loaded.target == '10.10.10.5'
assert loaded.config.profile == 'general'
assert loaded.config.budget == 5.0

print('end-to-end smoke OK')
"
rm -rf /tmp/reverser-stop-resume-test
```

Expected: prints `created session_id=...` and `end-to-end smoke OK`.

- [ ] **Step 7: Commit (only if cleanup needed)**

If everything passed, no commit. If you found a small typo, fix and:

```bash
git commit -am "chore: integration validation cleanup for stop & resume"
```

---

## Done

The stop/resume feature is implemented end-to-end:

- `targets/<target>/sessions/<id>.json` snapshots with atomic writes
- Lifecycle states: active → stopped → resumed → active OR active → completed
- Per-turn autosave + explicit stop button (f3 / /stop) + atexit + SIGTERM emergency snapshot
- CLI: top-level `--list-sessions`, `interactive --resume [SESSION_ID]`, `--force` for live takeover
- TUI: f3/f4/f5 keybindings, /stop /done /info /help slash commands, three modal dialogs
- Header shows session info; chat pane replays conversation on resume
- Manager-profile dispatch records `in_flight` (if manager work is present)
- Manual smoke test doc for human-in-loop validation

Final state:
- `src/reverser/sessions.py` — new module with snapshot dataclass + I/O + listing + ContextVar
- `src/reverser/tui/session.py` — refactored to support resume; new stop/mark_completed methods
- `src/reverser/tui/app.py` — new keybindings, slash commands, atexit hooks, header rendering
- `src/reverser/tui/modals/` — three new modal screens
- `src/reverser/cli.py` — top-level --list-sessions; interactive --resume + --force
- `src/reverser/session_log.py` — three new event kinds
- ~370+ passing tests, 1 skipped

Future work (per spec §15): conversation summary injection on resume, file locking, snapshot compression for very long sessions, cleanup commands, cross-host session export, TUI session picker, configurable per-target session limits, resume-into-different-profile.
