"""Bridge KB writes to the WS frame stream.

Each KB-mutating tool calls `emit_hypothesis(action, row)` or
`emit_finding(action, row)` after the SQLite write succeeds. The helper
looks up the current session via `reverser.sessions.current_session` and
fires `session.emit_kb_event(...)`. When there is no current session
(headless / CLI tool invocations), the call is a no-op.
"""
from __future__ import annotations
import dataclasses
from typing import Any

from ..sessions import current_session


def _row_to_dict(row: Any) -> dict:
    if dataclasses.is_dataclass(row):
        return dataclasses.asdict(row)
    if hasattr(row, "__dict__"):
        return {k: v for k, v in vars(row).items() if not k.startswith("_")}
    return dict(row)


def emit_hypothesis(action: str, row: Any) -> None:
    """Emit a `hypothesis` WS frame for a hypothesis create/update."""
    sess = current_session.get()
    if sess is None:
        return
    try:
        sess.emit_kb_event("hypothesis", {"action": action, "row": _row_to_dict(row)})
    except Exception:
        pass


def emit_finding(action: str, row: Any) -> None:
    """Emit a `finding` WS frame for a finding create/update."""
    sess = current_session.get()
    if sess is None:
        return
    try:
        sess.emit_kb_event("finding", {"action": action, "row": _row_to_dict(row)})
    except Exception:
        pass
