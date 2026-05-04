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
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

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


# ── Neo4j driver session ────────────────────────────────────────────

def _get_neo4j_driver(target: str):
    """Return a neo4j.GraphDatabase driver bound to the per-target instance.

    The caller is responsible for closing the driver. Reads the password from
    `targets/<target>/neo4j/bolt_password`.
    """
    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise RuntimeError(
            "neo4j Python driver is not installed. Add `neo4j` to the venv."
        ) from e

    pw_path = _password_file(target)
    if not pw_path.is_file():
        raise RuntimeError(
            f"Bolt password not found at {pw_path}. "
            f"Has bloodhound_start been run for this target?"
        )
    password = pw_path.read_text().strip()
    uri = f"bolt://{_BOLT_HOST}:{_BOLT_PORT}"
    return GraphDatabase.driver(uri, auth=(_NEO4J_DEFAULT_USER, password))


def _get_neo4j_session(target: str):
    """Convenience: open a driver and return (driver, session). Caller closes both."""
    driver = _get_neo4j_driver(target)
    return driver, driver.session()


# ── Write detection for free-form cypher ────────────────────────────

_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL\s+APOC\.CREATE|CALL\s+APOC\.MERGE|CALL\s+DBMS|CALL\s+DB\.CREATE)\b",
    re.IGNORECASE,
)


def _detect_writes(cypher: str) -> bool:
    """Return True if the cypher appears to contain a write operation.

    Intentionally over-broad: a naive regex (does not parse strings/comments).
    Callers who need writes pass allow_writes=True to the tool.
    """
    return _WRITE_RE.search(cypher) is not None


# ── Canned-query catalog (15 queries) ───────────────────────────────

CANNED_QUERIES: dict[str, str] = {
    "kerberoastable_users": """
        MATCH (u:User)
        WHERE u.hasspn = true AND u.enabled = true
        RETURN u.name AS name,
               u.serviceprincipalnames AS spns,
               u.pwdlastset AS pwdlastset
        ORDER BY u.name
    """.strip(),

    "asreproastable_users": """
        MATCH (u:User)
        WHERE u.dontreqpreauth = true AND u.enabled = true
        RETURN u.name AS name,
               u.pwdlastset AS pwdlastset
        ORDER BY u.name
    """.strip(),

    "shortest_path_to_da": """
        MATCH (g:Group)
        WHERE g.name STARTS WITH 'DOMAIN ADMINS@'
        MATCH p = shortestPath((src)-[*1..]->(g))
        WHERE NOT src = g
        RETURN p
        LIMIT 5
    """.strip(),

    "computers_where_user_admin": """
        MATCH (u {name: $username})-[:MemberOf*0..]->(g)-[:AdminTo]->(c:Computer)
        RETURN DISTINCT c.name AS computer
        ORDER BY computer
    """.strip(),

    "users_with_dcsync": """
        MATCH (u:User)-[:MemberOf*0..]->(g)-[:GetChanges|GetChangesAll|GetChangesInFilteredSet]->(d:Domain)
        RETURN DISTINCT u.name AS name, d.name AS domain
        ORDER BY name
    """.strip(),

    "unconstrained_delegation": """
        MATCH (n)
        WHERE (n:User OR n:Computer) AND n.unconstraineddelegation = true
        RETURN labels(n)[0] AS kind, n.name AS name
        ORDER BY kind, name
    """.strip(),

    "constrained_delegation": """
        MATCH (n)-[r:AllowedToDelegate]->(t:Computer)
        RETURN labels(n)[0] AS kind, n.name AS principal, t.name AS target
        ORDER BY principal, target
    """.strip(),

    "password_not_required": """
        MATCH (u:User)
        WHERE u.passwordnotreqd = true AND u.enabled = true
        RETURN u.name AS name
        ORDER BY u.name
    """.strip(),

    "computers_no_laps": """
        MATCH (c:Computer)
        WHERE c.haslaps = false AND c.enabled = true
        RETURN c.name AS name, c.operatingsystem AS os
        ORDER BY c.name
    """.strip(),

    "foreign_group_membership": """
        MATCH (u:User)-[:MemberOf]->(g:Group)
        WHERE NOT split(u.name, '@')[1] = split(g.name, '@')[1]
        RETURN u.name AS user, g.name AS group
        ORDER BY user
    """.strip(),

    "owned_to_high_value": """
        MATCH (u {name: $username})
        MATCH (target {highvalue: true})
        MATCH p = shortestPath((u)-[*1..]->(target))
        RETURN p
        LIMIT 10
    """.strip(),

    "sessions_on_target": """
        MATCH (u:User)-[:HasSession]->(c:Computer {name: $computer})
        RETURN u.name AS user, c.name AS computer
        ORDER BY user
    """.strip(),

    "high_value_targets": """
        MATCH (n)
        WHERE n.highvalue = true
        RETURN labels(n)[0] AS kind, n.name AS name
        ORDER BY kind, name
    """.strip(),

    "domain_admins": """
        MATCH (g:Group)
        WHERE g.name STARTS WITH 'DOMAIN ADMINS@'
        MATCH (u:User)-[:MemberOf*1..]->(g)
        RETURN DISTINCT u.name AS name, g.name AS group
        ORDER BY name
    """.strip(),

    "kerberos_delegation_summary": """
        MATCH (n)
        WHERE (n:User OR n:Computer) AND
              (n.unconstraineddelegation = true OR
               n.allowedtodelegate IS NOT NULL OR
               n.trustedtoauth = true)
        RETURN labels(n)[0] AS kind,
               n.name AS name,
               coalesce(n.unconstraineddelegation, false) AS unconstrained,
               coalesce(n.trustedtoauth, false) AS rbcd_eligible,
               size(coalesce(n.allowedtodelegate, [])) AS constrained_targets
        ORDER BY kind, name
    """.strip(),
}


# ── Neo4j launch ────────────────────────────────────────────────────

def _launch_neo4j(target: str) -> int:
    """Launch Neo4j for `target` in the background. Returns the PID."""
    data_dir = _neo4j_dir(target)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "data").mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    (data_dir / "conf").mkdir(exist_ok=True)
    (data_dir / "plugins").mkdir(exist_ok=True)
    (data_dir / "import").mkdir(exist_ok=True)
    (data_dir / "run").mkdir(exist_ok=True)

    password = _ensure_bolt_password(target)

    conf = data_dir / "conf" / "neo4j.conf"
    if not conf.is_file():
        conf.write_text(
            f"server.default_listen_address={_BOLT_HOST}\n"
            f"server.bolt.listen_address={_BOLT_HOST}:{_BOLT_PORT}\n"
            f"server.http.listen_address={_BOLT_HOST}:{_HTTP_PORT}\n"
            f"server.https.enabled=false\n"
            f"dbms.security.auth_minimum_password_length=1\n"
        )

    env = os.environ.copy()
    env["NEO4J_HOME"] = str(data_dir)
    env["NEO4J_CONF"] = str(data_dir / "conf")

    auth_marker = data_dir / "data" / "dbms" / "auth"
    if not auth_marker.exists():
        try:
            subprocess.run(
                ["neo4j-admin", "dbms", "set-initial-password", password],
                env=env,
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    log_file = data_dir / "logs" / "neo4j-launch.log"
    log_fh = open(log_file, "ab")
    proc = subprocess.Popen(
        ["neo4j", "console"],
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            log_tail = log_file.read_text(errors="replace")[-2000:]
            raise RuntimeError(
                f"Neo4j exited during startup (rc={proc.returncode}). "
                f"Log tail:\n{log_tail}"
            )
        if _is_port_in_use(_BOLT_PORT):
            return proc.pid
        time.sleep(0.5)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    log_tail = log_file.read_text(errors="replace")[-2000:]
    raise RuntimeError(
        f"Neo4j did not open bolt port {_BOLT_PORT} within 30s. "
        f"Log tail:\n{log_tail}"
    )


@tool(
    "bloodhound_start",
    "Start a per-target Neo4j instance for BloodHound. Creates the data dir, "
    "generates a random bolt password (stored at targets/<target>/neo4j/bolt_password), "
    "and launches Neo4j on bolt port 7687. Idempotent — returns the existing PID if "
    "already running for this target. Refuses if a different target's Neo4j is on 7687.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target identifier (IP, hostname, CIDR)"},
        },
        "required": ["target"],
    },
)
async def bloodhound_start(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target_input = args["target"]
    kb = for_target(target_input)
    target = kb.target_id
    data_dir = _neo4j_dir(target)
    data_dir.mkdir(parents=True, exist_ok=True)

    existing_pid = _read_pid(target)
    if existing_pid is not None and _process_alive(existing_pid):
        return format_tool_result(
            f"Neo4j already running for target {target}.\n"
            f"  PID: {existing_pid}\n"
            f"  Bolt: bolt://{_BOLT_HOST}:{_BOLT_PORT}\n"
            f"  Data dir: {data_dir}\n"
            f"  Password file: {_password_file(target)}"
        )

    if existing_pid is not None and not _process_alive(existing_pid):
        _clear_pid(target)

    if _is_port_in_use(_BOLT_PORT):
        return format_error(
            f"Bolt port {_BOLT_PORT} is already in use, but no PID file exists for "
            f"target {target}. Another target's Neo4j (or an unrelated process) is "
            f"running on this port. Stop it first with `bloodhound_stop <other_target>` "
            f"or kill the process manually."
        )

    try:
        pid = _launch_neo4j(target)
    except RuntimeError as e:
        return format_error(f"Failed to start Neo4j for {target}: {e}")

    _write_pid(target, pid)
    return format_tool_result(
        f"Neo4j started for target {target}.\n"
        f"  PID: {pid}\n"
        f"  Bolt: bolt://{_BOLT_HOST}:{_BOLT_PORT}\n"
        f"  Data dir: {data_dir}\n"
        f"  Password file: {_password_file(target)}\n"
        f"\nNext: run bloodhound_collect to populate the graph."
    )


# ── Neo4j shutdown ──────────────────────────────────────────────────

def _kill_process_group(pid: int, timeout: float = 15.0) -> bool:
    """Send SIGTERM to the process group of `pid`, waiting up to `timeout` for exit."""
    if not _process_alive(pid):
        return True
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return not _process_alive(pid)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.5)

    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    time.sleep(0.5)
    return not _process_alive(pid)


@tool(
    "bloodhound_stop",
    "Stop the Neo4j process for the given target. Data persists on disk.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target identifier"},
        },
        "required": ["target"],
    },
)
async def bloodhound_stop(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target_input = args["target"]
    kb = for_target(target_input)
    target = kb.target_id
    pid = _read_pid(target)
    if pid is None:
        return format_tool_result(f"Neo4j is not running for target {target} (no PID file).")

    if not _process_alive(pid):
        _clear_pid(target)
        return format_tool_result(
            f"Neo4j was not actually running for target {target} (stale PID {pid} cleared)."
        )

    success = _kill_process_group(pid)
    _clear_pid(target)
    if success:
        return format_tool_result(
            f"Neo4j stopped for target {target} (PID {pid} terminated). Data preserved at {_neo4j_dir(target)}."
        )
    return format_error(
        f"Sent SIGTERM/SIGKILL to PID {pid} but the process appears to still be alive. "
        f"Investigate manually."
    )
