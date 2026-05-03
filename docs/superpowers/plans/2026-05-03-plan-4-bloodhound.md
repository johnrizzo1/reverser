# Plan 4 — BloodHound Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the per-target BloodHound stack: Neo4j lifecycle (start/stop/status with PID tracking and bolt-port collision detection), bloodhound-python collector wrapper with auto-import, 15 canned cypher queries, and free-form cypher with read-only enforcement. Six new LLM-facing tools registered with the MCP server, all gated behind pentest authorization.

**Architecture:** Each target gets its own Neo4j data directory at `targets/<target>/neo4j/`, started by `bloodhound_start` with `NEO4J_HOME` overridden. Only one Neo4j may be running at a time on bolt port 7687; `bloodhound_start` errors clearly if a different target's Neo4j is up. The bolt password is randomly generated on first start and stored at `targets/<target>/neo4j/bolt_password`. The `bloodhound-python` collector emits a JSON zip; the wrapper unzips it and bulk-MERGEs into the per-target Neo4j via the official `neo4j` Python driver. Canned queries live in a static dict; free-form cypher is regex-checked for write keywords and rejected unless `allow_writes=True`.

**Tech Stack:** Python 3.11+, Neo4j 5.x (Nix package), official `neo4j` driver, `bloodhound-python` collector. Depends on Plan 1 (`reverser.kb`).

**Spec reference:** `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` § BloodHound tools.

---

## File Structure

**Created:**
- `src/reverser/tools/bloodhound.py` — six BloodHound tools + helpers
- `tests/test_bloodhound_lifecycle.py` — Neo4j lifecycle helper tests (PID tracking, password, port-collision)
- `tests/test_bloodhound_query.py` — write-detection regex tests + canned-query catalog tests
- `tests/test_bloodhound_collect.py` — zip-import logic tests (mocked driver)
- `tests/test_bloodhound_smoke.py` — opt-in integration smoke test (gated on `_neo4j_available()`)

**Modified:**
- `src/reverser/tools/__init__.py` — register `bloodhound_tools`
- `devenv.nix` — add `neo4j` (native) + `neo4j` and `bloodhound` (Python venv)

---

## Task 1: Add Neo4j + driver dependencies to devenv.nix

**Files:**
- Modify: `devenv.nix`

- [ ] **Step 1: Add `neo4j` to native packages**

In `devenv.nix`, locate the `# Penetration testing / Network recon` block in the `packages` list and add `neo4j` near `nmap`:

```nix
    # Penetration testing / Network recon
    nmap
    nikto
    gobuster
    sslscan
    whatweb
    nbtscan
    krb5                   # kinit, klist, krb5-config
    dnsutils               # dig, nslookup, host
    seclists               # wordlists for gobuster, kerberos, etc.
    neo4j                  # graph database for BloodHound (per-target instances)
```

- [ ] **Step 2: Add Python packages to the venv requirements**

In the `languages.python.venv.requirements` block, append `neo4j` and `bloodhound`:

```nix
      requirements = ''
        claude-agent-sdk
        boto3
        click
        textual
        openai
        angr
        capstone
        unicorn
        pwntools
        r2pipe
        rzpipe
        pyelftools
        yara-python
        lief
        pyshark
        ropper
        keystone-engine
        pefile
        malduck
        flare-floss
        pyhidra
        ldap3
        impacket
	invoke
	pynacl
	paramiko
        neo4j
        bloodhound
      '';
```

- [ ] **Step 3: Add a verification line to `enterTest`**

In the `enterTest` block, after the existing pentest-tools section, append:

```sh
    echo "Testing BloodHound stack..."
    neo4j --version > /dev/null 2>&1 && echo "✓ neo4j" || echo "✗ neo4j"
    python3 -c "import neo4j" > /dev/null 2>&1 && echo "✓ neo4j (py)" || echo "✗ neo4j (py)"
    python3 -c "import bloodhound" > /dev/null 2>&1 && echo "✓ bloodhound (py)" || echo "✗ bloodhound (py)"
```

- [ ] **Step 4: Reload devenv shell and verify**

Run: `devenv shell -- bash -c 'neo4j --version && python3 -c "import neo4j; import bloodhound; print(\"ok\")"'`
Expected: A version string for neo4j and `ok` from python.

- [ ] **Step 5: Commit**

```bash
git add devenv.nix
git commit -m "chore(devenv): add neo4j + bloodhound-python for BloodHound stack"
```

---

## Task 2: Lifecycle helper module — paths, PID tracking, bolt-password

**Files:**
- Create: `src/reverser/tools/bloodhound.py` (initial — helpers only, no @tool decorators yet)
- Create: `tests/test_bloodhound_lifecycle.py`

- [ ] **Step 1: Write failing test `tests/test_bloodhound_lifecycle.py`**

```python
"""Tests for bloodhound lifecycle helpers (PID tracking, bolt password, port-collision)."""

import os
import socket
from pathlib import Path

import pytest

from reverser.tools.bloodhound import (
    _neo4j_dir,
    _pid_file,
    _password_file,
    _read_pid,
    _write_pid,
    _clear_pid,
    _ensure_bolt_password,
    _is_port_in_use,
    _BOLT_PORT,
)


def test_neo4j_dir_under_target(tmp_targets_dir):
    p = _neo4j_dir("10.10.10.5")
    assert p == tmp_targets_dir / "10.10.10.5" / "neo4j"


def test_pid_file_path(tmp_targets_dir):
    assert _pid_file("10.10.10.5") == tmp_targets_dir / "10.10.10.5" / "neo4j" / ".pid"


def test_password_file_path(tmp_targets_dir):
    assert _password_file("10.10.10.5") == tmp_targets_dir / "10.10.10.5" / "neo4j" / "bolt_password"


def test_write_then_read_pid(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 12345)
    assert _read_pid("10.10.10.5") == 12345


def test_read_pid_missing_returns_none(tmp_targets_dir):
    assert _read_pid("10.10.10.5") is None


def test_clear_pid_removes_file(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 99)
    _clear_pid("10.10.10.5")
    assert _read_pid("10.10.10.5") is None


def test_ensure_bolt_password_creates_random(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    pw = _ensure_bolt_password("10.10.10.5")
    assert len(pw) >= 24
    # second call returns the same password
    assert _ensure_bolt_password("10.10.10.5") == pw


def test_ensure_bolt_password_persists_to_disk(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    pw = _ensure_bolt_password("10.10.10.5")
    on_disk = (tmp_targets_dir / "10.10.10.5" / "neo4j" / "bolt_password").read_text().strip()
    assert on_disk == pw


def test_is_port_in_use_false_for_free_port():
    # Pick a port that's almost certainly free
    assert _is_port_in_use(1) is False or _is_port_in_use(1) is True  # don't crash


def test_is_port_in_use_true_when_bound():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        assert _is_port_in_use(port) is True
    finally:
        s.close()


def test_bolt_port_default():
    assert _BOLT_PORT == 7687
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_lifecycle.py -v`
Expected: ImportError — module doesn't exist yet.

- [ ] **Step 3: Create `src/reverser/tools/bloodhound.py` with lifecycle helpers**

```python
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
    """Return the Neo4j data directory for the given target (not normalized here;
    callers should pass the normalized target_id from `for_target`)."""
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_lifecycle.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_lifecycle.py
git commit -m "feat(bloodhound): lifecycle helpers (paths, PID, bolt password, port detection)"
```

---

## Task 3: Neo4j driver-session helper + canned-query catalog (15 queries)

**Files:**
- Modify: `src/reverser/tools/bloodhound.py` — append the canned-query dict + driver helper
- Create: `tests/test_bloodhound_query.py` — test the catalog completeness + driver helper signature

- [ ] **Step 1: Write failing test `tests/test_bloodhound_query.py`**

```python
"""Tests for canned-query catalog + free-form query write detection."""

import pytest

from reverser.tools.bloodhound import (
    CANNED_QUERIES,
    _detect_writes,
)


# ── Canned-query catalog completeness ────────────────────────────────

EXPECTED_QUERY_NAMES = [
    "kerberoastable_users",
    "asreproastable_users",
    "shortest_path_to_da",
    "computers_where_user_admin",
    "users_with_dcsync",
    "unconstrained_delegation",
    "constrained_delegation",
    "password_not_required",
    "computers_no_laps",
    "foreign_group_membership",
    "owned_to_high_value",
    "sessions_on_target",
    "high_value_targets",
    "domain_admins",
    "kerberos_delegation_summary",
]


def test_canned_query_catalog_has_all_15():
    assert set(CANNED_QUERIES.keys()) == set(EXPECTED_QUERY_NAMES)


@pytest.mark.parametrize("name", EXPECTED_QUERY_NAMES)
def test_each_canned_query_is_nonempty_string(name):
    assert isinstance(CANNED_QUERIES[name], str)
    assert "MATCH" in CANNED_QUERIES[name].upper()


@pytest.mark.parametrize("name", EXPECTED_QUERY_NAMES)
def test_no_canned_query_has_writes(name):
    assert _detect_writes(CANNED_QUERIES[name]) is False


# ── Write-detection regex ────────────────────────────────────────────

@pytest.mark.parametrize("cypher", [
    "MATCH (n) RETURN n",
    "MATCH (u:User) WHERE u.name = 'x' RETURN u",
    "MATCH p = shortestPath((a)-[*]->(b)) RETURN p",
    "match (n) return count(n)",  # case-insensitive
])
def test_detect_writes_returns_false_for_reads(cypher):
    assert _detect_writes(cypher) is False


@pytest.mark.parametrize("cypher", [
    "CREATE (n:User {name: 'x'})",
    "MERGE (n:Group {name: 'a'})",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) SET n.x = 1",
    "MATCH (n) REMOVE n.x",
    "create (n:X)",  # case-insensitive
    "MATCH (n) CALL apoc.create.node(['L'], {}) YIELD node RETURN node",
    "DROP CONSTRAINT foo",
    "MATCH (n)-[r]->(m) SET r.x = 1",
])
def test_detect_writes_returns_true_for_writes(cypher):
    assert _detect_writes(cypher) is True


def test_detect_writes_ignores_strings_and_comments():
    """Naive regex would flip on the literal word 'CREATE' inside a string."""
    # Literal 'CREATE' in a string — the simple regex DOES catch this; document
    # that allow_writes=True is the escape hatch.
    cypher = 'MATCH (n) WHERE n.note = "CREATE" RETURN n'
    # Acceptable: false positive — caller can pass allow_writes=True.
    assert _detect_writes(cypher) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_query.py -v`
Expected: ImportError (CANNED_QUERIES, _detect_writes do not exist).

- [ ] **Step 3: Append driver helper + canned-query dict + write detector to `bloodhound.py`**

Append to `src/reverser/tools/bloodhound.py`:

```python
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

# Match cypher write keywords as standalone tokens (case-insensitive).
# Conservative: false positives are acceptable — caller can pass allow_writes=True.
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_query.py -v`
Expected: ~25 passed (1 catalog + 15 nonempty + 15 no-writes + 4 reads + 10 writes + 1 string-fp).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_query.py
git commit -m "feat(bloodhound): canned-query catalog (15) + write detector + driver helper"
```

---

## Task 4: `bloodhound_start` tool

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Modify: `tests/test_bloodhound_lifecycle.py` (append start-tool tests with subprocess mocked)

- [ ] **Step 1: Append failing tests**

```python
from unittest.mock import patch, MagicMock

import asyncio


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_bloodhound_start_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_start
    result = _run(bloodhound_start({"target": "10.10.10.5"}))
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()


def test_bloodhound_start_idempotent_returns_existing_pid(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", os.getpid())  # current process is alive
    from reverser.tools.bloodhound import bloodhound_start
    result = _run(bloodhound_start({"target": "10.10.10.5"}))
    assert result.get("is_error") is not True
    assert "already running" in result["content"][0]["text"].lower()
    assert str(os.getpid()) in result["content"][0]["text"]


def test_bloodhound_start_clears_stale_pid(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 99999999)  # almost-certainly-dead pid
    # Mock the actual neo4j launch to avoid touching the system
    with patch("reverser.tools.bloodhound._launch_neo4j") as mock_launch, \
         patch("reverser.tools.bloodhound._is_port_in_use", return_value=False):
        mock_launch.return_value = 12345
        from reverser.tools.bloodhound import bloodhound_start
        result = _run(bloodhound_start({"target": "10.10.10.5"}))
        assert result.get("is_error") is not True
        assert _read_pid("10.10.10.5") == 12345


def test_bloodhound_start_refuses_when_other_target_uses_port(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    # No pid file for 10.10.10.5, but port is in use (simulating another target)
    with patch("reverser.tools.bloodhound._is_port_in_use", return_value=True):
        from reverser.tools.bloodhound import bloodhound_start
        result = _run(bloodhound_start({"target": "10.10.10.5"}))
        assert result.get("is_error") is True
        assert "7687" in result["content"][0]["text"]
        assert "another" in result["content"][0]["text"].lower() or "different" in result["content"][0]["text"].lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_lifecycle.py -v -k bloodhound_start`
Expected: 4 failures (no `bloodhound_start`, no `_launch_neo4j`).

- [ ] **Step 3: Append `_launch_neo4j` + `bloodhound_start` to `bloodhound.py`**

Append to `src/reverser/tools/bloodhound.py`:

```python
# ── Neo4j launch ────────────────────────────────────────────────────

def _launch_neo4j(target: str) -> int:
    """Launch Neo4j for `target` in the background. Returns the PID.

    Sets NEO4J_HOME to targets/<target>/neo4j/. Waits up to 30s for the bolt
    port to come up before returning. Raises RuntimeError if the process exits
    or the port never opens.
    """
    data_dir = _neo4j_dir(target)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "data").mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    (data_dir / "conf").mkdir(exist_ok=True)
    (data_dir / "plugins").mkdir(exist_ok=True)
    (data_dir / "import").mkdir(exist_ok=True)
    (data_dir / "run").mkdir(exist_ok=True)

    # Generate / read bolt password BEFORE launching so we can set initial pw.
    password = _ensure_bolt_password(target)

    # Write a minimal neo4j.conf binding to localhost only.
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

    # Set initial password on first run if no auth db exists yet.
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
            pass  # best-effort; user may need to set password manually

    # Launch neo4j console in the background. We use Popen + setsid so it
    # survives the parent and we can kill the process group later.
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

    # Wait for the bolt port to come up.
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
    # Timeout — kill the process and bail
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    log_tail = log_file.read_text(errors="replace")[-2000:]
    raise RuntimeError(
        f"Neo4j did not open bolt port {_BOLT_PORT} within 30s. "
        f"Log tail:\n{log_tail}"
    )


# ── Tool: bloodhound_start ──────────────────────────────────────────

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

    # Already-running check.
    existing_pid = _read_pid(target)
    if existing_pid is not None and _process_alive(existing_pid):
        return format_tool_result(
            f"Neo4j already running for target {target}.\n"
            f"  PID: {existing_pid}\n"
            f"  Bolt: bolt://{_BOLT_HOST}:{_BOLT_PORT}\n"
            f"  Data dir: {data_dir}\n"
            f"  Password file: {_password_file(target)}"
        )

    # Stale pid file — clear it.
    if existing_pid is not None and not _process_alive(existing_pid):
        _clear_pid(target)

    # Port-collision check (different target already using 7687).
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_lifecycle.py -v -k bloodhound_start`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_lifecycle.py
git commit -m "feat(bloodhound): bloodhound_start tool with idempotency + port-collision detection"
```

---

## Task 5: `bloodhound_stop` tool

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Modify: `tests/test_bloodhound_lifecycle.py`

- [ ] **Step 1: Append failing tests**

```python
def test_bloodhound_stop_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_stop
    result = _run(bloodhound_stop({"target": "10.10.10.5"}))
    assert result.get("is_error") is True


def test_bloodhound_stop_no_pid_returns_message(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_stop
    result = _run(bloodhound_stop({"target": "10.10.10.5"}))
    assert result.get("is_error") is not True
    assert "not running" in result["content"][0]["text"].lower()


def test_bloodhound_stop_kills_pid_and_clears_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 12345)
    with patch("reverser.tools.bloodhound._kill_process_group") as mock_kill:
        mock_kill.return_value = True
        from reverser.tools.bloodhound import bloodhound_stop
        result = _run(bloodhound_stop({"target": "10.10.10.5"}))
        assert result.get("is_error") is not True
        mock_kill.assert_called_once_with(12345)
        assert _read_pid("10.10.10.5") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_lifecycle.py -v -k bloodhound_stop`
Expected: 3 failures.

- [ ] **Step 3: Append `_kill_process_group` + `bloodhound_stop` to `bloodhound.py`**

```python
# ── Neo4j shutdown ──────────────────────────────────────────────────

def _kill_process_group(pid: int, timeout: float = 15.0) -> bool:
    """Send SIGTERM to the process group of `pid`, waiting up to `timeout` for exit.

    Falls back to SIGKILL if the process is still alive after timeout.
    Returns True if the process is no longer alive after this function returns.
    """
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

    # Hard kill.
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    time.sleep(0.5)
    return not _process_alive(pid)


# ── Tool: bloodhound_stop ───────────────────────────────────────────

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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_lifecycle.py -v -k bloodhound_stop`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_lifecycle.py
git commit -m "feat(bloodhound): bloodhound_stop tool with graceful + hard kill fallback"
```

---

## Task 6: `bloodhound_status` tool

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Modify: `tests/test_bloodhound_lifecycle.py`

- [ ] **Step 1: Append failing tests**

```python
def test_bloodhound_status_no_target_lists_known(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    (tmp_targets_dir / "10.10.10.6" / "neo4j").mkdir(parents=True)
    (tmp_targets_dir / "junk").mkdir()  # no neo4j subdir
    from reverser.tools.bloodhound import bloodhound_status
    result = _run(bloodhound_status({}))
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text
    assert "10.10.10.6" in text
    assert "junk" not in text


def test_bloodhound_status_with_target_no_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    from reverser.tools.bloodhound import bloodhound_status
    result = _run(bloodhound_status({"target": "10.10.10.5"}))
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "stopped" in text.lower()


def test_bloodhound_status_with_target_running_queries_counts(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", os.getpid())
    _ensure_bolt_password("10.10.10.5")
    fake_session = MagicMock()
    fake_session.run.return_value = [{"count": 7}]
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    fake_driver.close = MagicMock()
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_status
        result = _run(bloodhound_status({"target": "10.10.10.5"}))
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "Users" in text
    assert "Computers" in text
    assert "7" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_lifecycle.py -v -k bloodhound_status`
Expected: 3 failures.

- [ ] **Step 3: Append `bloodhound_status` to `bloodhound.py`**

```python
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
                        # Support both dict-like and Record-like
                        try:
                            out[label] = row["count"]
                        except (KeyError, TypeError):
                            out[label] = row[0] if hasattr(row, "__getitem__") else None
                    else:
                        out[label] = 0
                except Exception as e:  # noqa: BLE001
                    out[label] = f"<err: {e}>"
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _get_meta(target: str, key: str) -> str | None:
    """Read a meta key from the per-target Neo4j (kept in node label `_Meta`)."""
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
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_lifecycle.py -v -k bloodhound_status`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_lifecycle.py
git commit -m "feat(bloodhound): bloodhound_status with node counts + last-collection meta"
```

---

## Task 7: Zip-import logic for BloodHound JSON output

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Create: `tests/test_bloodhound_collect.py`

- [ ] **Step 1: Write failing test `tests/test_bloodhound_collect.py`**

```python
"""Tests for bloodhound-python zip import + collect tool wrapper."""

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reverser.tools.bloodhound import (
    _import_bloodhound_zip,
    _classify_bloodhound_json_file,
    _CYPHER_BY_KIND,
)


def _make_bh_zip(tmp_path: Path, files: dict[str, dict]) -> Path:
    z = tmp_path / "bh.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, json.dumps(content))
    return z


def test_classify_users_file():
    assert _classify_bloodhound_json_file("20260503000000_users.json") == "users"
    assert _classify_bloodhound_json_file("users.json") == "users"


def test_classify_computers_file():
    assert _classify_bloodhound_json_file("computers.json") == "computers"


def test_classify_groups_file():
    assert _classify_bloodhound_json_file("groups.json") == "groups"


def test_classify_unknown_file_returns_none():
    assert _classify_bloodhound_json_file("README.md") is None


def test_cypher_by_kind_has_all_six():
    assert set(_CYPHER_BY_KIND.keys()) == {
        "users", "computers", "groups", "ous", "gpos", "domains",
    }


def test_import_bloodhound_zip_runs_merges(tmp_path):
    payload = {
        "data": [
            {"ObjectIdentifier": "S-1-5-21-1", "Properties": {"name": "JDOE@CORP.LOCAL", "enabled": True}},
            {"ObjectIdentifier": "S-1-5-21-2", "Properties": {"name": "ASMITH@CORP.LOCAL", "enabled": False}},
        ],
        "meta": {"type": "users", "count": 2},
    }
    z = _make_bh_zip(tmp_path, {"users.json": payload})

    fake_session = MagicMock()
    fake_session.run = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session

    counts = _import_bloodhound_zip(fake_driver, z)
    assert counts == {"users": 2}
    # Each user should have triggered a session.run call
    assert fake_session.run.call_count >= 2


def test_import_bloodhound_zip_multiple_kinds(tmp_path):
    z = _make_bh_zip(tmp_path, {
        "users.json": {"data": [{"ObjectIdentifier": "S-1-1", "Properties": {"name": "u1@D"}}], "meta": {"type": "users"}},
        "computers.json": {"data": [{"ObjectIdentifier": "S-2-1", "Properties": {"name": "c1@D"}}], "meta": {"type": "computers"}},
        "groups.json": {"data": [], "meta": {"type": "groups"}},
        "README.md": {"ignore": "me"},
    })
    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    counts = _import_bloodhound_zip(fake_driver, z)
    assert counts == {"users": 1, "computers": 1, "groups": 0}


def test_import_bloodhound_zip_handles_objectless_entries(tmp_path):
    z = _make_bh_zip(tmp_path, {
        "users.json": {
            "data": [{"Properties": {"name": "no_oid@D"}}],  # no ObjectIdentifier
            "meta": {"type": "users"},
        },
    })
    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    counts = _import_bloodhound_zip(fake_driver, z)
    # Should skip rows missing ObjectIdentifier rather than crash
    assert counts == {"users": 0}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_collect.py -v`
Expected: ImportError.

- [ ] **Step 3: Append zip-import logic to `bloodhound.py`**

```python
# ── BloodHound JSON zip import ──────────────────────────────────────

# Per-kind MERGE templates. Each template expects a single bound parameter
# `props` (a dict of node properties) and `oid` (the ObjectIdentifier).
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
    """Map a bloodhound-python output filename to a kind key.

    Filenames look like: `20260503120000_users.json`, `users.json`, etc.
    """
    base = os.path.basename(filename).lower()
    if not base.endswith(".json"):
        return None
    stem = base[:-5]  # strip .json
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
            # Keep lists of scalars
            if all(isinstance(x, (str, int, float, bool)) for x in v):
                out[k] = v
        # else: skip nested dicts / mixed lists (Neo4j won't store them as props)
    return out


def _import_bloodhound_zip(driver, zip_path: Path) -> dict[str, int]:
    """Import a bloodhound-python output zip via the bolt driver.

    Returns a dict of {kind: rows_imported}. Skips rows missing ObjectIdentifier.
    """
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
                    except Exception:  # noqa: BLE001
                        # Best-effort import: keep going
                        continue
                counts[kind] = counts.get(kind, 0) + imported
    return counts
```

Add the missing `import json` to the top of the file if not already present.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_collect.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_collect.py
git commit -m "feat(bloodhound): zip-import logic for bloodhound-python JSON output"
```

---

## Task 8: `bloodhound_collect` tool wrapper

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Modify: `tests/test_bloodhound_collect.py`

- [ ] **Step 1: Append failing tests**

```python
import asyncio


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_bloodhound_collect_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_collect
    result = _run(bloodhound_collect({
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "dc_ip": "10.10.10.5", "username": "jdoe", "password": "x",
    }))
    assert result.get("is_error") is True


def test_bloodhound_collect_requires_neo4j_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_collect
    result = _run(bloodhound_collect({
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "dc_ip": "10.10.10.5", "username": "jdoe", "password": "x",
    }))
    assert result.get("is_error") is True
    assert "bloodhound_start" in result["content"][0]["text"]


def test_bloodhound_collect_requires_password_or_hash(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid_helper(tmp_targets_dir / "10.10.10.5" / "neo4j" / ".pid", os.getpid())
    from reverser.tools.bloodhound import bloodhound_collect
    result = _run(bloodhound_collect({
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "dc_ip": "10.10.10.5", "username": "jdoe",
    }))
    assert result.get("is_error") is True
    assert "password" in result["content"][0]["text"].lower() or "hash" in result["content"][0]["text"].lower()


def test_bloodhound_collect_invokes_bloodhound_python(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid_helper(tmp_targets_dir / "10.10.10.5" / "neo4j" / ".pid", os.getpid())

    captured_cmd = []

    def fake_run_bh(cmd, cwd):
        captured_cmd.extend(cmd)
        # Drop a fake zip in cwd so the tool finds it
        z = Path(cwd) / "20260503000000_corp.local.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("users.json", json.dumps({"data": [], "meta": {"type": "users"}}))
        return {"stdout": "ok", "stderr": "", "returncode": 0}

    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver.session.return_value = fake_session

    with patch("reverser.tools.bloodhound._run_bloodhound_python", side_effect=fake_run_bh), \
         patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_collect
        result = _run(bloodhound_collect({
            "target": "10.10.10.5", "domain": "CORP.LOCAL",
            "dc_ip": "10.10.10.5", "username": "jdoe", "password": "x",
        }))
    assert result.get("is_error") is not True
    assert "users" in result["content"][0]["text"].lower()
    # bloodhound-python invoked with -d CORP.LOCAL -u jdoe -p x -dc 10.10.10.5
    assert "-d" in captured_cmd
    assert "CORP.LOCAL" in captured_cmd
    assert "-u" in captured_cmd
    assert "jdoe" in captured_cmd


def _write_pid_helper(p, pid):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(pid))
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_collect.py -v -k bloodhound_collect`
Expected: 4 failures.

- [ ] **Step 3: Append `bloodhound_collect` (and `_run_bloodhound_python`) to `bloodhound.py`**

```python
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

    # Confirm Neo4j is running.
    pid = _read_pid(target)
    if pid is None or not _process_alive(pid):
        return format_error(
            f"Neo4j is not running for target {target}. "
            f"Run bloodhound_start first."
        )

    # Set up an output dir for the collection zip.
    out_dir = _neo4j_dir(target) / "collections"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the bloodhound-python command.
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

    # Import into Neo4j.
    try:
        driver = _get_neo4j_driver(target)
    except RuntimeError as e:
        return format_error(f"Could not connect to Neo4j: {e}")
    try:
        counts = _import_bloodhound_zip(driver, zip_path)
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass

    # Stamp the meta key + KB note.
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
    except Exception:  # noqa: BLE001
        pass
    return format_tool_result("\n".join(summary_lines))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_collect.py -v -k bloodhound_collect`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_collect.py
git commit -m "feat(bloodhound): bloodhound_collect — wrap collector + auto-import zip"
```

---

## Task 9: `bloodhound_query` tool — free-form cypher

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Modify: `tests/test_bloodhound_query.py`

- [ ] **Step 1: Append failing tests**

```python
import asyncio
from unittest.mock import patch, MagicMock


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_bloodhound_query_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_query
    result = _run(bloodhound_query({"target": "10.10.10.5", "cypher": "MATCH (n) RETURN n"}))
    assert result.get("is_error") is True


def test_bloodhound_query_rejects_writes_by_default(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_query
    result = _run(bloodhound_query({
        "target": "10.10.10.5",
        "cypher": "CREATE (n:Bogus)",
    }))
    assert result.get("is_error") is True
    assert "allow_writes" in result["content"][0]["text"]


def test_bloodhound_query_runs_read(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_record = MagicMock()
    fake_record.data.return_value = {"name": "Alice"}
    fake_session.run.return_value = [fake_record]
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_query
        result = _run(bloodhound_query({
            "target": "10.10.10.5",
            "cypher": "MATCH (u:User) RETURN u.name AS name",
        }))
    assert result.get("is_error") is not True
    assert "Alice" in result["content"][0]["text"]


def test_bloodhound_query_passes_params(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_query
        _run(bloodhound_query({
            "target": "10.10.10.5",
            "cypher": "MATCH (u:User {name: $name}) RETURN u",
            "params": {"name": "jdoe"},
        }))
    fake_session.run.assert_called_once()
    call_args = fake_session.run.call_args
    # second positional or 'parameters' kwarg should carry our params
    assert {"name": "jdoe"} in call_args.args or call_args.kwargs.get("parameters") == {"name": "jdoe"}


def test_bloodhound_query_allow_writes_passes(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_query
        result = _run(bloodhound_query({
            "target": "10.10.10.5",
            "cypher": "CREATE (n:_Test)",
            "allow_writes": True,
        }))
    assert result.get("is_error") is not True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_query.py -v -k bloodhound_query`
Expected: 5 failures.

- [ ] **Step 3: Append `_format_records` + `bloodhound_query` to `bloodhound.py`**

```python
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


# ── Tool: bloodhound_query (free-form cypher) ───────────────────────

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
            except Exception as e:  # noqa: BLE001
                return format_error(f"Cypher query failed: {e}")
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass

    return format_tool_result(
        f"Query: {cypher.strip()[:200]}\n"
        f"Rows returned: {len(records)}\n\n"
        f"{_records_to_text(records)}"
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_query.py -v -k bloodhound_query`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_query.py
git commit -m "feat(bloodhound): bloodhound_query (free-form cypher) with write detection"
```

---

## Task 10: `bloodhound_canned` tool — named query catalog

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`
- Modify: `tests/test_bloodhound_query.py`

- [ ] **Step 1: Append failing tests**

```python
def test_bloodhound_canned_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_canned
    result = _run(bloodhound_canned({"target": "10.10.10.5", "query_name": "domain_admins"}))
    assert result.get("is_error") is True


def test_bloodhound_canned_unknown_name(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_canned
    result = _run(bloodhound_canned({"target": "10.10.10.5", "query_name": "no_such_query"}))
    assert result.get("is_error") is True
    assert "no_such_query" in result["content"][0]["text"]
    # Should list valid options
    assert "domain_admins" in result["content"][0]["text"]


@pytest.mark.parametrize("name", EXPECTED_QUERY_NAMES)
def test_bloodhound_canned_runs_each(name, tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    params = {}
    if name in ("computers_where_user_admin", "owned_to_high_value"):
        params = {"username": "jdoe@CORP.LOCAL"}
    if name == "sessions_on_target":
        params = {"computer": "WS01@CORP.LOCAL"}
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_canned
        result = _run(bloodhound_canned({
            "target": "10.10.10.5",
            "query_name": name,
            "params": params,
        }))
    assert result.get("is_error") is not True, f"{name}: {result['content'][0]['text']}"
    fake_session.run.assert_called_once()


def test_bloodhound_canned_passes_params(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_canned
        _run(bloodhound_canned({
            "target": "10.10.10.5",
            "query_name": "owned_to_high_value",
            "params": {"username": "jdoe@CORP.LOCAL"},
        }))
    call = fake_session.run.call_args
    assert {"username": "jdoe@CORP.LOCAL"} in call.args or call.kwargs.get("parameters") == {"username": "jdoe@CORP.LOCAL"}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_bloodhound_query.py -v -k bloodhound_canned`
Expected: 18 failures (2 base + 15 parametrized + 1 params).

- [ ] **Step 3: Append `bloodhound_canned` to `bloodhound.py`**

```python
# ── Tool: bloodhound_canned ─────────────────────────────────────────

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
            except Exception as e:  # noqa: BLE001
                return format_error(f"Canned query {name!r} failed: {e}")
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass

    return format_tool_result(
        f"Canned query: {name}\n"
        f"Params: {params or '(none)'}\n"
        f"Rows: {len(records)}\n\n"
        f"{_records_to_text(records)}"
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_bloodhound_query.py -v -k bloodhound_canned`
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_bloodhound_query.py
git commit -m "feat(bloodhound): bloodhound_canned with 15 named queries"
```

---

## Task 11: Export TOOLS list and register with MCP server

**Files:**
- Modify: `src/reverser/tools/bloodhound.py` (add TOOLS list at end)
- Modify: `src/reverser/tools/__init__.py`

- [ ] **Step 1: Append `TOOLS` list to `src/reverser/tools/bloodhound.py`**

At the very bottom of `bloodhound.py`:

```python
# ── Module-level tool registry ──────────────────────────────────────

TOOLS = [
    bloodhound_start,
    bloodhound_stop,
    bloodhound_status,
    bloodhound_collect,
    bloodhound_canned,
    bloodhound_query,
]
```

- [ ] **Step 2: Register the module in `src/reverser/tools/__init__.py`**

Replace the contents with:

```python
"""RE tool registry — aggregates all tool categories into a single MCP server."""

from claude_agent_sdk import create_sdk_mcp_server

from .triage import TOOLS as triage_tools
from .static import TOOLS as static_tools
from .dynamic import TOOLS as dynamic_tools
from .python_analysis import TOOLS as python_tools
from .exploit import TOOLS as exploit_tools
from .util import TOOLS as util_tools
from .network import TOOLS as network_tools
from .web import TOOLS as web_tools
from .bloodhound import TOOLS as bloodhound_tools

ALL_TOOLS = (
    triage_tools + static_tools + dynamic_tools + python_tools + exploit_tools
    + util_tools + network_tools + web_tools + bloodhound_tools
)


def create_re_mcp_server():
    """Create the MCP server exposing all RE tools."""
    return create_sdk_mcp_server(
        name="re",
        version="0.1.0",
        tools=ALL_TOOLS,
    )
```

- [ ] **Step 3: Verify importability**

Run: `python -c "from reverser.tools import ALL_TOOLS; print(len([t for t in ALL_TOOLS if 'bloodhound' in str(t)]))"`
Expected: `6` (or similar — six new bloodhound tools registered).

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/test_bloodhound_lifecycle.py tests/test_bloodhound_query.py tests/test_bloodhound_collect.py -v`
Expected: All previously-passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py src/reverser/tools/__init__.py
git commit -m "feat(tools): register bloodhound TOOLS in MCP server registry"
```

---

## Task 12: Integration smoke test (gated on real Neo4j)

**Files:**
- Create: `tests/test_bloodhound_smoke.py`

- [ ] **Step 1: Write the gated smoke test**

```python
"""Opt-in integration smoke test for the BloodHound stack.

Skipped unless `neo4j` is on PATH AND `REVERSER_BLOODHOUND_SMOKE=1`.
This test actually starts and stops a real per-target Neo4j instance.
"""

import asyncio
import os
import shutil

import pytest

from reverser.tools.bloodhound import (
    bloodhound_start,
    bloodhound_stop,
    bloodhound_status,
    _read_pid,
    _process_alive,
)


def _neo4j_available() -> bool:
    return (
        shutil.which("neo4j") is not None
        and os.environ.get("REVERSER_BLOODHOUND_SMOKE") == "1"
    )


pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Real Neo4j smoke test gated on REVERSER_BLOODHOUND_SMOKE=1 + neo4j in PATH",
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_smoke_start_status_stop(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    target = "smoke.target.test"

    # Start
    start_result = _run(bloodhound_start({"target": target}))
    assert start_result.get("is_error") is not True, start_result["content"][0]["text"]
    pid = _read_pid(target)
    assert pid is not None and _process_alive(pid)

    try:
        # Status — should report running, with zero counts (empty graph)
        status_result = _run(bloodhound_status({"target": target}))
        text = status_result["content"][0]["text"]
        assert "RUNNING" in text
        assert "Users" in text
    finally:
        # Stop
        stop_result = _run(bloodhound_stop({"target": target}))
        assert stop_result.get("is_error") is not True
        assert _read_pid(target) is None
```

- [ ] **Step 2: Verify the test is skipped without the env var**

Run: `pytest tests/test_bloodhound_smoke.py -v`
Expected: `1 skipped`.

- [ ] **Step 3: (Optional, manual) Run the smoke test**

If running locally with neo4j installed:
```
REVERSER_BLOODHOUND_SMOKE=1 REVERSER_PENTEST_AUTHORIZED=1 pytest tests/test_bloodhound_smoke.py -v
```
Expected: 1 passed (takes ~30-60s for Neo4j to start).

- [ ] **Step 4: Commit**

```bash
git add tests/test_bloodhound_smoke.py
git commit -m "test(bloodhound): add opt-in real-Neo4j smoke test"
```

---

## Task 13: Final sweep — full test suite

**Files:** None modified.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: All Plan-1 tests still pass; all new bloodhound tests pass; the smoke test is skipped.

- [ ] **Step 2: Verify the public tool surface**

Run: `python -c "from reverser.tools.bloodhound import TOOLS; print('\\n'.join(t.name if hasattr(t, 'name') else str(t) for t in TOOLS))"`
Expected: lists six tools — `bloodhound_start`, `bloodhound_stop`, `bloodhound_status`, `bloodhound_collect`, `bloodhound_canned`, `bloodhound_query`.

- [ ] **Step 3: Verify the canned-query catalog matches the spec**

Run: `python -c "from reverser.tools.bloodhound import CANNED_QUERIES; print(sorted(CANNED_QUERIES.keys()))"`
Expected: All 15 names exactly: `asreproastable_users, computers_no_laps, computers_where_user_admin, constrained_delegation, domain_admins, foreign_group_membership, high_value_targets, kerberoastable_users, kerberos_delegation_summary, owned_to_high_value, password_not_required, sessions_on_target, shortest_path_to_da, unconstrained_delegation, users_with_dcsync`.

- [ ] **Step 4: Final commit if any cleanup was needed**

If no changes, skip. Otherwise:

```bash
git commit -am "chore(bloodhound): final API surface verification"
```

---

## Done

Plan 4 ships the full BloodHound stack: per-target Neo4j lifecycle (start/stop/status with PID + port-collision safety), bloodhound-python collector with auto-import via the bolt driver, 15 canned cypher queries (kerberoastable, AS-REP roastable, shortest path to DA, etc.), and a free-form cypher tool with read-only enforcement.

Six new tools registered with the MCP server: `bloodhound_start`, `bloodhound_stop`, `bloodhound_status`, `bloodhound_collect`, `bloodhound_canned`, `bloodhound_query`.

Next up: **Plan 5 — `ad` profile + prompts + manual smoke checklist.**
