"""BloodHound tools: per-target Neo4j lifecycle, bloodhound-python collector,
canned cypher queries, free-form cypher.

All tools are gated by `require_pentest_auth()`. Each target gets its own Neo4j
data directory under `targets/<target>/neo4j/`. Only one Neo4j can be running at
a time on bolt port 7687; the lifecycle helpers detect collisions and refuse to
double-start.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import socket
import subprocess
import time
import zipfile
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


# ── Tool: bloodhound_status ─────────────────────────────────────────

_STATUS_NODE_QUERIES = {
    "Users": "MATCH (u:User) RETURN count(u) AS count",
    "Computers": "MATCH (c:Computer) RETURN count(c) AS count",
    "Groups": "MATCH (g:Group) RETURN count(g) AS count",
    "OUs": "MATCH (o:OU) RETURN count(o) AS count",
    "GPOs": "MATCH (g:GPO) RETURN count(g) AS count",
    "Domains": "MATCH (d:Domain) RETURN count(d) AS count",
}


def _list_known_targets() -> list[str]:
    root = _targets_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "neo4j").is_dir()
    )


def _query_node_counts(target: str) -> dict[str, Any]:
    """Run the status node-count queries. Returns a dict label -> count or error str."""
    out: dict[str, Any] = {}
    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError as e:
        return {"_error": str(e)}
    try:
        with driver.session() as session:
            for label, cypher in _STATUS_NODE_QUERIES.items():
                try:
                    rows = list(session.run(cypher))
                    if rows:
                        row = rows[0]
                        try:
                            out[label] = row["count"]
                        except (KeyError, TypeError):
                            out[label] = row[0] if hasattr(row, "__getitem__") else None
                    else:
                        out[label] = 0
                except Exception as e:
                    out[label] = f"<err: {e}>"
    finally:
        try:
            driver.close()
        except Exception:
            pass
    return out


def _get_meta(target: str, key: str) -> str | None:
    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError:
        return None
    try:
        with driver.session() as session:
            rows = list(session.run(
                "MATCH (m:_Meta {key: $k}) RETURN m.value AS value",
                {"k": key},
            ))
            if not rows:
                return None
            row = rows[0]
            try:
                return row["value"]
            except (KeyError, TypeError):
                return row[0]
    except Exception:
        return None
    finally:
        try:
            driver.close()
        except Exception:
            pass


def _set_meta(target: str, key: str, value: str) -> None:
    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError:
        return
    try:
        with driver.session() as session:
            session.run(
                "MERGE (m:_Meta {key: $k}) SET m.value = $v",
                {"k": key, "v": value},
            )
    except Exception:
        pass
    finally:
        try:
            driver.close()
        except Exception:
            pass


@tool(
    "bloodhound_status",
    "Report Neo4j status for a target (running PID, port, data dir, node counts, "
    "last-collection timestamp). Without `target`, lists all targets that have a Neo4j data dir.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Optional target identifier"},
        },
    },
)
async def bloodhound_status(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target_input = args.get("target") or ""
    if not target_input.strip():
        targets = _list_known_targets()
        if not targets:
            return format_tool_result("No targets with a Neo4j data dir under targets/.")
        lines = ["Known BloodHound targets (have a neo4j/ subdir):"]
        for t in targets:
            pid = _read_pid(t)
            running = pid is not None and _process_alive(pid)
            status = f"running (PID {pid})" if running else "stopped"
            lines.append(f"  - {t} [{status}]")
        return format_tool_result("\n".join(lines))

    kb = for_target(target_input)
    target = kb.target_id
    pid = _read_pid(target)
    running = pid is not None and _process_alive(pid)
    data_dir = _neo4j_dir(target)

    lines = [f"Target: {target}"]
    lines.append(f"  Data dir:  {data_dir}")
    lines.append(f"  PID file:  {_pid_file(target)}")
    lines.append(f"  Bolt port: {_BOLT_PORT}")

    if not running:
        lines.append("  Status:    NOT RUNNING (start with bloodhound_start)")
        return format_tool_result("\n".join(lines))

    lines.append(f"  Status:    RUNNING (PID {pid})")
    counts = _query_node_counts(target)
    if counts.get("_error"):
        lines.append(f"  Node counts: <error: {counts['_error']}>")
    else:
        lines.append("  Node counts:")
        for label, val in counts.items():
            lines.append(f"    {label:10s} {val}")
    last_collection = _get_meta(target, _META_LAST_COLLECTION)
    if last_collection:
        lines.append(f"  Last collection: {last_collection}")
    else:
        lines.append("  Last collection: <never> (run bloodhound_collect)")
    return format_tool_result("\n".join(lines))


# ── BloodHound JSON zip import ──────────────────────────────────────

_CYPHER_BY_KIND: dict[str, str] = {
    "users": """
        MERGE (u:User {objectid: $oid})
        SET u += $props
    """.strip(),
    "computers": """
        MERGE (c:Computer {objectid: $oid})
        SET c += $props
    """.strip(),
    "groups": """
        MERGE (g:Group {objectid: $oid})
        SET g += $props
    """.strip(),
    "ous": """
        MERGE (o:OU {objectid: $oid})
        SET o += $props
    """.strip(),
    "gpos": """
        MERGE (g:GPO {objectid: $oid})
        SET g += $props
    """.strip(),
    "domains": """
        MERGE (d:Domain {objectid: $oid})
        SET d += $props
    """.strip(),
}


def _classify_bloodhound_json_file(filename: str) -> str | None:
    """Map a bloodhound-python output filename to a kind key."""
    base = os.path.basename(filename).lower()
    if not base.endswith(".json"):
        return None
    stem = base[:-5]
    for kind in ("users", "computers", "groups", "ous", "gpos", "domains"):
        if stem == kind or stem.endswith(f"_{kind}"):
            return kind
    return None


def _flatten_props(props: dict | None) -> dict:
    """Flatten a BloodHound Properties dict into Neo4j-safe scalar/list types."""
    if not isinstance(props, dict):
        return {}
    out = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list):
            if all(isinstance(x, (str, int, float, bool)) for x in v):
                out[k] = v
    return out


def _import_bloodhound_zip(driver, zip_path: Path) -> dict[str, int]:
    """Import a bloodhound-python output zip via the bolt driver."""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        with driver.session() as session:
            for member in zf.namelist():
                kind = _classify_bloodhound_json_file(member)
                if kind is None:
                    continue
                cypher = _CYPHER_BY_KIND[kind]
                try:
                    raw = zf.read(member).decode("utf-8")
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    counts.setdefault(kind, 0)
                    continue
                rows = payload.get("data", []) if isinstance(payload, dict) else []
                imported = 0
                for entry in rows:
                    if not isinstance(entry, dict):
                        continue
                    oid = entry.get("ObjectIdentifier")
                    if not oid:
                        continue
                    props = _flatten_props(entry.get("Properties"))
                    try:
                        session.run(cypher, {"oid": oid, "props": props})
                        imported += 1
                    except Exception:
                        continue
                counts[kind] = counts.get(kind, 0) + imported
    return counts


# ── bloodhound-python collector wrapper ─────────────────────────────

def _run_bloodhound_python(cmd: list[str], cwd: str) -> dict:
    """Invoke bloodhound-python. Pulled out for test mocking."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=cwd,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "bloodhound-python timed out after 600s", "returncode": -1}
    except FileNotFoundError:
        return {"stdout": "", "stderr": "bloodhound-python not found in PATH", "returncode": -1}


def _find_collection_zip(directory: Path) -> Path | None:
    """Find the most recent bloodhound-python output zip in `directory`."""
    candidates = sorted(
        directory.glob("*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


@tool(
    "bloodhound_collect",
    "Run the bloodhound-python collector against a domain controller and import "
    "the results into the per-target Neo4j. Requires a running Neo4j (call "
    "bloodhound_start first). The collector zip is auto-imported via the bolt driver.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target identifier"},
            "domain": {"type": "string", "description": "AD domain (e.g. CORP.LOCAL)"},
            "dc_ip": {"type": "string", "description": "Domain controller IP (used as -dc and -ns)"},
            "username": {"type": "string", "description": "Domain user for collection"},
            "password": {"type": "string", "description": "Password (or use nt_hash)", "default": ""},
            "nt_hash": {"type": "string", "description": "NT hash (alternative to password)", "default": ""},
            "collection_methods": {
                "type": "string",
                "description": "BloodHound collection methods (default: Default,LoggedOn). "
                               "Use 'DCOnly' for stealthier runs.",
                "default": "Default,LoggedOn",
            },
        },
        "required": ["target", "domain", "dc_ip", "username"],
    },
)
async def bloodhound_collect(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target_input = args["target"]
    domain = args["domain"]
    dc_ip = args["dc_ip"]
    username = args["username"]
    password = args.get("password", "") or ""
    nt_hash = args.get("nt_hash", "") or ""
    methods = args.get("collection_methods", "") or "Default,LoggedOn"

    if not password and not nt_hash:
        return format_error(
            "bloodhound_collect requires either `password` or `nt_hash` for the user."
        )

    kb = for_target(target_input)
    target = kb.target_id

    pid = _read_pid(target)
    if pid is None or not _process_alive(pid):
        return format_error(
            f"Neo4j is not running for target {target}. "
            f"Run bloodhound_start first."
        )

    out_dir = _neo4j_dir(target) / "collections"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "bloodhound-python",
        "-c", methods,
        "-d", domain,
        "-u", username,
        "-dc", dc_ip,
        "-ns", dc_ip,
        "--zip",
    ]
    if password:
        cmd += ["-p", password]
    if nt_hash:
        cmd += ["--hashes", f":{nt_hash}"]

    proc_result = _run_bloodhound_python(cmd, cwd=str(out_dir))
    if proc_result["returncode"] != 0:
        return format_error(
            f"bloodhound-python failed (rc={proc_result['returncode']}):\n"
            f"stdout: {proc_result['stdout'][:1000]}\n"
            f"stderr: {proc_result['stderr'][:2000]}"
        )

    zip_path = _find_collection_zip(out_dir)
    if zip_path is None:
        return format_error(
            f"bloodhound-python ran successfully but no .zip was produced in {out_dir}."
        )

    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError as e:
        return format_error(f"Could not connect to Neo4j: {e}")
    try:
        counts = _import_bloodhound_zip(driver, zip_path)
    finally:
        try:
            driver.close()
        except Exception:
            pass

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _set_meta(target, _META_LAST_COLLECTION, now_iso)

    summary_lines = [f"BloodHound collection complete for {target} ({domain}):"]
    for kind in ("users", "computers", "groups", "ous", "gpos", "domains"):
        summary_lines.append(f"  {kind:10s} {counts.get(kind, 0)}")
    summary_lines.append(f"  zip: {zip_path}")
    try:
        kb.record_note(
            f"BloodHound collection ({methods}) into Neo4j for {domain}: " +
            ", ".join(f"{k}={v}" for k, v in counts.items())
        )
    except Exception:
        pass
    return format_tool_result("\n".join(summary_lines))


# ── Formatting helpers for cypher result tables ─────────────────────

def _records_to_text(records: list, max_rows: int = 50) -> str:
    """Render a list of neo4j Record/dict-like objects as a simple table."""
    if not records:
        return "(no rows)"
    rows = []
    for r in records:
        try:
            rows.append(r.data())
        except AttributeError:
            try:
                rows.append(dict(r))
            except (TypeError, ValueError):
                rows.append({"_": str(r)})
    if not rows:
        return "(no rows)"

    keys: list[str] = []
    for row in rows:
        for k in row.keys():
            if k not in keys:
                keys.append(k)

    truncated = False
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        truncated = True

    header = " | ".join(keys)
    sep = "-+-".join("-" * len(k) for k in keys)
    lines = [header, sep]
    for row in rows:
        lines.append(" | ".join(str(row.get(k, "")) for k in keys))
    if truncated:
        lines.append(f"... ({len(records) - max_rows} more row(s) elided)")
    return "\n".join(lines)


@tool(
    "bloodhound_query",
    "Run a free-form cypher query against the per-target Neo4j. Read-only by default; "
    "writes (CREATE/MERGE/DELETE/SET/REMOVE) require allow_writes=True.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target identifier"},
            "cypher": {"type": "string", "description": "Cypher query"},
            "params": {
                "type": "object",
                "description": "Optional parameters dict for $name placeholders",
                "default": {},
            },
            "allow_writes": {
                "type": "boolean",
                "description": "Permit write keywords (CREATE/MERGE/DELETE/SET/REMOVE). Default false.",
                "default": False,
            },
        },
        "required": ["target", "cypher"],
    },
)
async def bloodhound_query(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target_input = args["target"]
    cypher = args["cypher"]
    params = args.get("params") or {}
    allow_writes = bool(args.get("allow_writes", False))

    if not allow_writes and _detect_writes(cypher):
        return format_error(
            "Cypher contains a write keyword (CREATE/MERGE/DELETE/SET/REMOVE/DROP/CALL apoc.create...). "
            "Pass `allow_writes=true` to permit. Note: this is intentionally over-broad — even "
            "string literals containing these words will trip it."
        )

    kb = for_target(target_input)
    target = kb.target_id
    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError as e:
        return format_error(f"Could not open Neo4j driver: {e}")

    try:
        with driver.session() as session:
            try:
                result = session.run(cypher, params)
                records = list(result)
            except Exception as e:
                return format_error(f"Cypher query failed: {e}")
    finally:
        try:
            driver.close()
        except Exception:
            pass

    return format_tool_result(
        f"Query: {cypher.strip()[:200]}\n"
        f"Rows returned: {len(records)}\n\n"
        f"{_records_to_text(records)}"
    )


@tool(
    "bloodhound_canned",
    "Run one of the 15 pre-canned BloodHound cypher queries against the per-target Neo4j. "
    "Some queries take parameters (e.g. owned_to_high_value needs $username). "
    "Available queries: " + ", ".join(sorted(CANNED_QUERIES.keys())),
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target identifier"},
            "query_name": {
                "type": "string",
                "description": "Canned query name (see tool description)",
                "enum": sorted(CANNED_QUERIES.keys()),
            },
            "params": {
                "type": "object",
                "description": "Optional parameters dict for $name placeholders in the cypher",
                "default": {},
            },
        },
        "required": ["target", "query_name"],
    },
)
async def bloodhound_canned(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target_input = args["target"]
    name = args["query_name"]
    params = args.get("params") or {}

    cypher = CANNED_QUERIES.get(name)
    if cypher is None:
        return format_error(
            f"Unknown canned query: {name!r}. "
            f"Available: {', '.join(sorted(CANNED_QUERIES.keys()))}"
        )

    kb = for_target(target_input)
    target = kb.target_id
    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError as e:
        return format_error(f"Could not open Neo4j driver: {e}")
    try:
        with driver.session() as session:
            try:
                records = list(session.run(cypher, params))
            except Exception as e:
                return format_error(f"Canned query {name!r} failed: {e}")
    finally:
        try:
            driver.close()
        except Exception:
            pass

    return format_tool_result(
        f"Canned query: {name}\n"
        f"Params: {params or '(none)'}\n"
        f"Rows: {len(records)}\n\n"
        f"{_records_to_text(records)}"
    )
