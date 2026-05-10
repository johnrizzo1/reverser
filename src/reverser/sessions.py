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
    """Return current time as ISO-8601 with seconds precision, UTC."""
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


def list_for_target(
    target: str, *, exclude_completed: bool = False
) -> list[SessionSnapshot]:
    """Enumerate snapshots for a target, sorted by last_active_at desc.

    Skips orphan .tmp files (incomplete writes from crashes) and
    silently skips corrupted snapshot files.
    """
    sessions_dir = _targets_root() / target / "sessions"
    if not sessions_dir.is_dir():
        return []

    snapshots: list[SessionSnapshot] = []
    for entry in sessions_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            data = json.loads(entry.read_text())
            snap = _from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
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
    """Most recent snapshot for the target. Default excludes completed."""
    snaps = list_for_target(target, exclude_completed=exclude_completed)
    return snaps[0] if snaps else None


def latest_global(
    *, exclude_completed: bool = True
) -> Optional[SessionSnapshot]:
    """Most recent snapshot across all targets."""
    snaps = list_all(exclude_completed=exclude_completed)
    return snaps[0] if snaps else None


def is_session_alive(snapshot: SessionSnapshot) -> bool:
    """True iff snapshot.pid is set AND that process exists.

    Uses os.kill(pid, 0) — sends signal 0 (no-op probe). Catches OSError
    when the process doesn't exist. Note: false positives possible if
    PID has been reused by an unrelated process. Best-effort only.
    """
    if snapshot.pid is None:
        return False
    try:
        os.kill(snapshot.pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
