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
