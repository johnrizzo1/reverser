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
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, TYPE_CHECKING
from urllib.parse import urlparse

from reverser.paths import targets_root

if TYPE_CHECKING:
    from reverser.tui.session import Session


SessionState = Literal["active", "stopped", "completed", "abandoned"]
SCHEMA_VERSION = 1

# Terminal states — sessions in these states are not offered for resume by
# default. `completed` = user explicitly marked done; `abandoned` = TUI
# exited without ever incrementing turns (typically: launched and quit
# without sending any messages).
_TERMINAL_STATES = ("completed", "abandoned")


@dataclass
class SessionConfig:
    profile: str
    backend: str = "claude"
    model: Optional[str] = None
    api_base: Optional[str] = None
    # Adversarial hypothesis validation (opt-in). None = no adversary runs.
    validation_backend: Optional[str] = None
    validation_model: Optional[str] = None
    validation_api_base: Optional[str] = None
    token_cost_per_1k: float = 0.0
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
    # Structured per-turn events captured during the exchange: thinking,
    # text, tool_call, tool_result. Used to rebuild a faithful resume
    # context (so the agent doesn't restart from scratch after a stop).
    # Each entry is a small dict — see _build_prompt for the shape.
    events: list[dict] = field(default_factory=list)


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
    archived_at: Optional[str] = None

    config: SessionConfig = field(
        default_factory=lambda: SessionConfig(profile="general")
    )
    stats: SessionStats = field(default_factory=SessionStats)
    conversation: list[ConversationEntry] = field(default_factory=list)
    ui: UIState = field(default_factory=UIState)

    in_flight: Optional[InFlightDispatch] = None
    pid: Optional[int] = None       # set while running; cleared on clean stop

    schema_version: int = SCHEMA_VERSION

    # NEW — preferred logical identity (Target.name); falls back to `target` if empty
    target_name: str = ""
    # NEW — address pinned at session start (Address.id)
    active_address_id: str = ""


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


def make_session_id() -> str:
    """Filename-safe ISO timestamp for a session ID.

    Format: YYYY-MM-DDTHH-MM-SS (colons in the time portion replaced with
    hyphens because POSIX filenames can technically contain colons but
    several tools and shells choke on them).
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M-%S")


def new_snapshot(
    *, target: str, log_path: str, config: SessionConfig,
    session_id: str | None = None,
    target_name: str = "",
    active_address_id: str = "",
) -> SessionSnapshot:
    """Construct a fresh snapshot for a new session.

    state=active, pid=os.getpid(), timestamps populated. Caller may pass
    `session_id` to override the default ISO-timestamp id (used by the GUI
    service so the snapshot id matches the id the manager hands back to
    the client).

    `target_name` and `active_address_id` are optional logical-identity fields
    linking the snapshot to a named Target and its pinned Address.
    """
    now = _now_iso()
    return SessionSnapshot(
        session_id=session_id if session_id is not None else make_session_id(),
        target=target,
        log_path=log_path,
        state="active",
        started_at=now,
        last_active_at=now,
        config=config,
        pid=os.getpid(),
        target_name=target_name,
        active_address_id=active_address_id,
    )


# Allowed chars: letters, digits, dot, dash, underscore, colon (for IPv6/ports)
_CANONICAL_TARGET_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

# Replacement for non-canonical chars in fallback mode
_SCRUB_RE = re.compile(r"[^A-Za-z0-9._:-]+")

# Max length for a target key (filesystem-friendly; well under typical 255 limit)
_MAX_TARGET_KEY_LEN = 64


def target_key(target: str) -> str:
    """Derive a filesystem-safe directory name from a target identifier.

    Handling, in priority order:
      1. Absolute filesystem path → take basename (e.g. /tmp/binary → binary)
      2. URL with scheme (http://, https://, ftp://) → take netloc (host[:port])
      3. CIDR notation (x.y.z.w/N) → take the network portion before `/`
      4. Otherwise → scrub non-allowed chars to `_`, clamp length

    All results are lowercased; clamped to _MAX_TARGET_KEY_LEN; stripped of
    leading/trailing _.- chars. Raises ValueError on empty input or if
    sanitization reduces to empty.

    See docs/superpowers/specs/2026-05-12-manager-reliability-design.md §9.
    """
    if not target or not target.strip():
        raise ValueError("target identifier must be non-empty")

    target = target.strip()

    # 1. Absolute path → basename (existing behavior)
    if os.path.isabs(target):
        target = os.path.basename(target)

    # 2. URL with scheme → netloc only
    elif target.startswith(("http://", "https://", "ftp://")):
        parsed = urlparse(target)
        if parsed.netloc:
            target = parsed.netloc
        # else: malformed URL, fall through to scrub

    # 3. CIDR → network portion
    elif "/" in target:
        # Heuristic: looks like IP/N CIDR? Take left of slash.
        left, _, _ = target.partition("/")
        if left and re.match(r"^\d+\.\d+\.\d+\.\d+$", left):
            target = left
        else:
            # Path-like input that isn't an absolute path and isn't a CIDR.
            # Scrub the whole thing.
            target = _SCRUB_RE.sub("_", target)

    # 4. Final scrub for anything still containing disallowed chars
    if not _CANONICAL_TARGET_RE.fullmatch(target):
        target = _SCRUB_RE.sub("_", target)

    # Clamp length (preserve right side — usually more distinguishing)
    if len(target) > _MAX_TARGET_KEY_LEN:
        target = target[-_MAX_TARGET_KEY_LEN:]

    # Lowercase to match normalize_target convention
    target = target.lower().strip("_.-")

    if not target:
        raise ValueError("target identifier reduced to empty string after sanitization")

    return target


def _is_canonical_target_name(name: str) -> bool:
    """Return True if a directory name matches the canonical target-key regex.

    Used by list_all() to filter bogus dirs from prior CLI parsing bugs
    (e.g. 'http:', '10.129.244.0' with '/24' subdir, free-text directories).

    Additional constraint beyond _CANONICAL_TARGET_RE: names must not end with
    a colon (e.g. 'http:' is a scheme artifact, not a valid host).
    """
    if not _CANONICAL_TARGET_RE.fullmatch(name):
        return False
    # Reject URL-scheme artifacts like 'http:' or 'ftp:'
    if name.endswith(":"):
        return False
    return True


def snapshot_path(target: str, session_id: str) -> Path:
    """Canonical path for a session snapshot file."""
    return targets_root() / target_key(target) / "sessions" / f"{session_id}.json"


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
        conversation=[
            ConversationEntry(
                user=e["user"], agent=e["agent"], turn=e["turn"],
                timestamp=e["timestamp"], cost=e["cost"],
                # Old snapshots written before the schema bump don't have
                # `events`; default to [].
                events=e.get("events", []),
            )
            for e in conversation_data
        ],
        ui=UIState(**ui_data) if ui_data else UIState(),
        in_flight=InFlightDispatch(**in_flight_data) if in_flight_data else None,
        pid=d.get("pid"),
        schema_version=d.get("schema_version", 1),
        target_name=d.get("target_name", ""),
        active_address_id=d.get("active_address_id", ""),
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
    sessions_dir = targets_root() / target_key(target) / "sessions"
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
    """Walk targets/*/sessions/, return all parsed snapshots, sorted desc.

    Skips directories that don't match the canonical target-name regex —
    these are bogus dirs from prior CLI parsing bugs (URLs as paths, free-
    text targets, etc.). See _is_canonical_target_name. Also skips any
    dot-prefixed directory (e.g. .trash/) — those are not targets.
    """
    root = targets_root()
    if not root.is_dir():
        return []

    all_snaps: list[SessionSnapshot] = []
    for target_dir in root.iterdir():
        if not target_dir.is_dir():
            continue
        # Skip hidden directories (.trash/, etc.) — never targets
        if target_dir.name.startswith("."):
            continue
        # Skip bogus dirs from prior CLI parsing bugs
        if not _is_canonical_target_name(target_dir.name):
            continue
        all_snaps.extend(
            list_for_target(target_dir.name, exclude_completed=exclude_completed)
        )

    all_snaps.sort(key=lambda s: s.last_active_at, reverse=True)
    return all_snaps


def latest_for_target(
    target: str,
    *,
    exclude_completed: bool = True,
    exclude_abandoned: bool = True,
    prefer_nonempty: bool = True,
) -> Optional[SessionSnapshot]:
    """Most recent snapshot for the target.

    Defaults exclude terminal states (completed, abandoned) — terminal
    sessions are not meant to be resumed.

    With prefer_nonempty=True (default), prefers the most recent session
    that has at least one turn over a more-recent zero-turn session. This
    matches the typical user intent ("resume my work, not the empty TUI
    launch from 30 seconds ago"). Falls back to the most recent of any
    eligible session if all eligible sessions are empty.
    """
    return _pick_latest(
        list_for_target(target),
        exclude_completed=exclude_completed,
        exclude_abandoned=exclude_abandoned,
        prefer_nonempty=prefer_nonempty,
    )


def latest_global(
    *,
    exclude_completed: bool = True,
    exclude_abandoned: bool = True,
    prefer_nonempty: bool = True,
) -> Optional[SessionSnapshot]:
    """Most recent snapshot across all targets. Same filtering as latest_for_target."""
    return _pick_latest(
        list_all(),
        exclude_completed=exclude_completed,
        exclude_abandoned=exclude_abandoned,
        prefer_nonempty=prefer_nonempty,
    )


def _pick_latest(
    snaps: list[SessionSnapshot],
    *,
    exclude_completed: bool,
    exclude_abandoned: bool,
    prefer_nonempty: bool,
) -> Optional[SessionSnapshot]:
    """Apply terminal-state filters and (optionally) the nonempty preference."""
    eligible = [
        s for s in snaps
        if not (exclude_completed and s.state == "completed")
        and not (exclude_abandoned and s.state == "abandoned")
    ]
    if not eligible:
        return None
    if prefer_nonempty:
        nonempty = [s for s in eligible if s.stats.turns > 0]
        if nonempty:
            return nonempty[0]  # already sorted desc by last_active_at
    return eligible[0]


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


def set_archived(target: str, session_id: str, archived: bool) -> None:
    """Set or clear the archived_at timestamp on an existing snapshot.

    Loads, mutates, saves. Refuses if the snapshot is actively running
    (state == 'active' AND pid is alive); a stale 'active' from a crashed
    process is treated as inactive so it can still be archived.
    """
    snap = load(target, session_id)
    if snap.state == "active" and is_session_alive(snap):
        raise SessionStateError(
            f"cannot archive an active session ({session_id}); stop it first"
        )
    snap.archived_at = _now_iso() if archived else None
    save(snap)


def delete(target: str, session_id: str) -> None:
    """Unlink a snapshot and its log file. Refuses if the session is
    actively running (state == 'active' AND pid is alive); a stale
    'active' from a crashed process is treated as inactive so it can
    still be deleted.

    The log file is best-effort: if it doesn't exist or is unreadable we
    log a warning and continue. The snapshot delete is the primary effect.
    """
    import logging

    snap = load(target, session_id)
    if snap.state == "active" and is_session_alive(snap):
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
