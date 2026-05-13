"""Per-target connection-failure circuit breaker.

Tracks consecutive connection errors against each target across ALL tool
families. After 3 consecutive failures, the breaker is "tripped" — subsequent
tool calls against the same target return an immediate error result instead
of running the tool. The breaker only resets when the user sends a new
message to the agent (signaled by `reset_for_target` or `reset_all`).

This prevents the "target unreachable death loop" antipattern observed in
the 10.129.60.148 engagement post-mortem (30 minutes of ping/nmap/curl
probes after the HTB VM dropped).

See docs/superpowers/specs/2026-05-12-manager-reliability-design.md §8.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from threading import Lock


# Module-level state — per-process, per-target counters
_CONN_FAILURE_THRESHOLD = 3
_lock = Lock()
_counters: dict[str, list[str]] = {}  # target → list of ISO timestamps

# Pattern matches against tool result text (stderr or stdout) for conn errors.
# Conservative: must be a clear network-level failure, not a TLS error or
# HTTP 5xx (which mean the target IS up).
_CONN_ERROR_RE = re.compile(
    r"connection\s+refused"
    r"|connection\s+timed\s+out"
    r"|connection\s+timeout"
    r"|no\s+route\s+to\s+host"
    r"|network\s+is\s+unreachable"
    r"|host\s+unreachable"
    r"|name\s+or\s+service\s+not\s+known"
    r"|nodename\s+nor\s+servname\s+provided"
    r"|could\s+not\s+resolve\s+host"
    r"|operation\s+timed\s+out"
    r"|ECONNREFUSED|EHOSTUNREACH|ENETUNREACH",
    re.IGNORECASE,
)


def looks_like_conn_error(text: str | None) -> bool:
    """Return True if the given subprocess output looks like a connection error.

    Heuristic-based: matches against well-known network-error phrases. Avoids
    matching TLS / HTTP-protocol errors (which mean the target IS up).
    """
    if not text:
        return False
    return bool(_CONN_ERROR_RE.search(text))


def record_failure(target: str) -> None:
    """Increment the consecutive-failure counter for `target`."""
    if not target:
        return
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        _counters.setdefault(target, []).append(ts)


def is_tripped(target: str) -> bool:
    """Return True if `target` has met or exceeded the failure threshold."""
    if not target:
        return False
    with _lock:
        return len(_counters.get(target, [])) >= _CONN_FAILURE_THRESHOLD


def failure_summary(target: str) -> dict:
    """Return {count, timestamps} for `target` (for the trip error message)."""
    with _lock:
        ts_list = list(_counters.get(target, []))
    return {"count": len(ts_list), "timestamps": ts_list}


def reset_for_target(target: str) -> None:
    """Clear the counter for `target`. Used by tests; production uses reset_all()."""
    if not target:
        return
    with _lock:
        _counters.pop(target, None)


def reset_all() -> None:
    """Clear all counters. Called on user input (the 'yield acknowledged' signal)."""
    with _lock:
        _counters.clear()
