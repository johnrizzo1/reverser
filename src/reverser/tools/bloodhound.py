"""BloodHound tools: per-target Neo4j lifecycle, bloodhound-python collector,
canned cypher queries, free-form cypher.

All tools are gated by `require_pentest_auth()`. Each target gets its own Neo4j
data directory under `targets/<target>/neo4j/`. Only one Neo4j can be running at
a time on bolt port 7687; the lifecycle helpers detect collisions and refuse to
double-start.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
from pathlib import Path

from claude_agent_sdk import tool

from ..kb import for_target, require_pentest_auth, AuthorizationError
from ._common import format_tool_result, format_error


# ── Constants ───────────────────────────────────────────────────────
_BOLT_PORT = 7687
_HTTP_PORT = 7474
_BOLT_HOST = "127.0.0.1"
_NEO4J_DEFAULT_USER = "neo4j"
_PID_FILENAME = ".pid"
_PASSWORD_FILENAME = "bolt_password"
_META_LAST_COLLECTION = "bloodhound:last_collection"


# ── Path helpers ────────────────────────────────────────────────────

def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


def _neo4j_dir(target: str) -> Path:
    """Return the Neo4j data directory for the given target."""
    return _targets_root() / target / "neo4j"


def _pid_file(target: str) -> Path:
    return _neo4j_dir(target) / _PID_FILENAME


def _password_file(target: str) -> Path:
    return _neo4j_dir(target) / _PASSWORD_FILENAME


# ── PID tracking ────────────────────────────────────────────────────

def _read_pid(target: str) -> int | None:
    p = _pid_file(target)
    if not p.is_file():
        return None
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_pid(target: str, pid: int) -> None:
    p = _pid_file(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(pid))


def _clear_pid(target: str) -> None:
    p = _pid_file(target)
    if p.is_file():
        p.unlink()


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── Bolt password ───────────────────────────────────────────────────

def _ensure_bolt_password(target: str) -> str:
    """Read the bolt password for target; generate + persist if missing."""
    p = _password_file(target)
    if p.is_file():
        existing = p.read_text().strip()
        if existing:
            return existing
    p.parent.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(24)
    p.write_text(pw)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return pw


# ── Port-collision detection ────────────────────────────────────────

def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if `host:port` already has a TCP listener."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        result = s.connect_ex((host, port))
        return result == 0
    except OSError:
        return False
    finally:
        s.close()
