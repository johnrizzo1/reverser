# Plan 1 — KB Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the per-target persistent SQLite knowledge base library (`reverser.kb`) that subsequent plans consume. No user-facing change yet — produces a tested library with a stable public API.

**Architecture:** A single `KB` class instance per target lives at `targets/<target>/state.db`. Public API (`reverser.kb.for_target`, `kb.record_*`, `kb.get_*`) hides SQLite. Schema migrations versioned in a `meta` table. WAL mode enabled for concurrent tool calls. All paths derived from `REVERSER_TARGETS_DIR` env var (default: `./targets/`).

**Tech Stack:** Python 3.11+, sqlite3 stdlib, dataclasses, pytest (added).

**Spec reference:** `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` § Architecture, § KB schema.

---

## File Structure

**Created:**
- `src/reverser/kb/__init__.py` — public API re-exports
- `src/reverser/kb/schema.py` — DDL + migrations
- `src/reverser/kb/store.py` — `KB` class, dataclasses, connection mgmt
- `src/reverser/kb/authz.py` — `require_pentest_auth()` shared helper
- `tests/__init__.py` — empty
- `tests/conftest.py` — pytest fixtures (`tmp_targets_dir`, `kb`)
- `tests/test_kb_schema.py` — schema/migration tests
- `tests/test_kb_store.py` — public API behavior tests
- `tests/test_kb_authz.py` — authz helper tests

**Modified:**
- `pyproject.toml` — add `pytest` to dev deps, add `[tool.pytest.ini_options]`
- `.gitignore` — add `targets/` and `*.db-wal`/`*.db-shm`

---

## Task 1: Test infrastructure setup

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `.gitignore`

- [ ] **Step 1: Add pytest config to `pyproject.toml`**

Append after the existing `[tool.setuptools.packages.find]` block:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create empty `tests/__init__.py`**

```python
```

- [ ] **Step 3: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures for the reverser test suite."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_targets_dir(tmp_path, monkeypatch):
    """Set REVERSER_TARGETS_DIR to a tmp dir for the duration of the test."""
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    return targets_dir


@pytest.fixture
def kb(tmp_targets_dir):
    """Return a fresh KB instance for the test target '10.10.10.5'."""
    from reverser.kb import for_target
    return for_target("10.10.10.5")
```

- [ ] **Step 4: Append to `.gitignore`**

If `.gitignore` does not exist, create it. Otherwise append:

```
# Per-target engagement data (sensitive)
targets/

# SQLite WAL/SHM files
*.db-wal
*.db-shm

# Python test artifacts
.pytest_cache/
__pycache__/
```

- [ ] **Step 5: Install dev deps and verify pytest runs**

Run: `pip install -e '.[dev]'` (inside the devenv shell)
Then: `pytest --collect-only`
Expected: `collected 0 items` (no errors).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/conftest.py .gitignore
git commit -m "chore: add pytest infrastructure for kb tests"
```

---

## Task 2: Authz helper

**Files:**
- Create: `src/reverser/kb/__init__.py` (empty stub for now)
- Create: `src/reverser/kb/authz.py`
- Create: `tests/test_kb_authz.py`

- [ ] **Step 1: Create empty package `__init__.py`**

`src/reverser/kb/__init__.py`:
```python
"""Per-target persistent knowledge base for reverser engagements."""
```

- [ ] **Step 2: Write failing test `tests/test_kb_authz.py`**

```python
"""Tests for the pentest authorization helper."""

import os
import pytest

from reverser.kb.authz import require_pentest_auth, AuthorizationError


def test_env_var_grants_auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    require_pentest_auth()  # should not raise


def test_authorized_file_grants_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".reverser-authorized").touch()
    require_pentest_auth()  # should not raise


def test_no_auth_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AuthorizationError) as exc_info:
        require_pentest_auth()
    assert "REVERSER_PENTEST_AUTHORIZED" in str(exc_info.value)
    assert ".reverser-authorized" in str(exc_info.value)


def test_env_var_other_value_does_not_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "0")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AuthorizationError):
        require_pentest_auth()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_kb_authz.py -v`
Expected: 4 errors with `ImportError` (module doesn't exist yet).

- [ ] **Step 4: Implement `src/reverser/kb/authz.py`**

```python
"""Pentest authorization gate shared by all active-testing tools."""

import os


class AuthorizationError(RuntimeError):
    """Raised when pentest authorization is required but not present."""


def require_pentest_auth() -> None:
    """Raise AuthorizationError unless pentest authorization is granted.

    Authorization sources (either is sufficient):
    - REVERSER_PENTEST_AUTHORIZED=1 environment variable
    - .reverser-authorized file in the current working directory
    """
    if os.environ.get("REVERSER_PENTEST_AUTHORIZED") == "1":
        return
    if os.path.exists(".reverser-authorized"):
        return
    raise AuthorizationError(
        "Pentest authorization required. "
        "Set REVERSER_PENTEST_AUTHORIZED=1 or create a .reverser-authorized "
        "file in the working directory to confirm you have written authorization "
        "to test the target."
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_kb_authz.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/__init__.py src/reverser/kb/authz.py tests/test_kb_authz.py
git commit -m "feat(kb): add pentest authorization helper"
```

---

## Task 3: Schema DDL + meta table

**Files:**
- Create: `src/reverser/kb/schema.py`
- Create: `tests/test_kb_schema.py`

- [ ] **Step 1: Write failing test `tests/test_kb_schema.py`**

```python
"""Tests for KB schema DDL and migration logic."""

import sqlite3
import pytest

from reverser.kb.schema import SCHEMA_VERSION, apply_schema, get_schema_version


def test_apply_schema_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    assert "targets" in tables
    assert "hosts" in tables
    assert "services" in tables
    assert "credentials" in tables
    assert "cred_results" in tables
    assert "findings" in tables
    assert "artifacts" in tables
    assert "notes" in tables
    assert "meta" in tables
    conn.close()


def test_schema_version_recorded(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    assert get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_apply_schema_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    apply_schema(conn)  # second call must not raise
    assert get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_credentials_unique_constraint(tmp_path):
    """Same (target, username, password, nt_hash) tuple cannot be inserted twice."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO targets (id, first_seen, last_active) VALUES (?, ?, ?)",
        ("10.10.10.5", "2026-05-03T00:00:00", "2026-05-03T00:00:00"),
    )
    conn.execute(
        "INSERT INTO credentials (target_id, username, password, status, first_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        ("10.10.10.5", "jdoe", "secret", "untested", "2026-05-03T00:00:00"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO credentials (target_id, username, password, status, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            ("10.10.10.5", "jdoe", "secret", "untested", "2026-05-03T00:00:00"),
        )
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kb_schema.py -v`
Expected: errors with `ImportError` (schema module doesn't exist).

- [ ] **Step 3: Implement `src/reverser/kb/schema.py`**

```python
"""SQLite schema DDL and lightweight migrations for the per-target KB."""

import sqlite3

SCHEMA_VERSION = 1

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS targets (
        id           TEXT PRIMARY KEY,
        hostname     TEXT,
        ip           TEXT,
        domain       TEXT,
        scope_notes  TEXT,
        first_seen   TEXT NOT NULL,
        last_active  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hosts (
        target_id    TEXT NOT NULL REFERENCES targets(id),
        ip           TEXT NOT NULL,
        hostname     TEXT,
        os           TEXT,
        domain       TEXT,
        is_dc        INTEGER NOT NULL DEFAULT 0,
        smb_signing  TEXT,
        first_seen   TEXT NOT NULL,
        PRIMARY KEY (target_id, ip)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS services (
        target_id   TEXT NOT NULL REFERENCES targets(id),
        host_ip     TEXT NOT NULL,
        port        INTEGER NOT NULL,
        proto       TEXT NOT NULL,
        service     TEXT,
        version     TEXT,
        banner      TEXT,
        scan_source TEXT,
        scanned_at  TEXT NOT NULL,
        PRIMARY KEY (target_id, host_ip, port, proto)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS credentials (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id       TEXT NOT NULL REFERENCES targets(id),
        username        TEXT NOT NULL,
        password        TEXT,
        nt_hash         TEXT,
        lm_hash         TEXT,
        kerberos_ticket TEXT,
        domain          TEXT,
        source_tool     TEXT,
        source_context  TEXT,
        status          TEXT NOT NULL,
        first_seen      TEXT NOT NULL,
        last_tested     TEXT,
        UNIQUE (target_id, username, password, nt_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cred_results (
        cred_id      INTEGER NOT NULL REFERENCES credentials(id),
        service_kind TEXT NOT NULL,
        target_host  TEXT NOT NULL,
        success      INTEGER NOT NULL,
        error_msg    TEXT,
        attempted_at TEXT NOT NULL,
        PRIMARY KEY (cred_id, service_kind, target_host, attempted_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id      TEXT NOT NULL REFERENCES targets(id),
        title          TEXT NOT NULL,
        severity       TEXT NOT NULL,
        cvss           REAL,
        description    TEXT,
        evidence_paths TEXT,
        created_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id   TEXT NOT NULL REFERENCES targets(id),
        kind        TEXT NOT NULL,
        path        TEXT NOT NULL,
        sha256      TEXT,
        source_tool TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id  TEXT NOT NULL REFERENCES targets(id),
        body       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
]


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if missing and stamp the schema version."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    for stmt in _DDL:
        conn.execute(stmt)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the schema version recorded in the meta table, or 0 if absent."""
    cursor = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    row = cursor.fetchone()
    return int(row[0]) if row else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kb_schema.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/schema.py tests/test_kb_schema.py
git commit -m "feat(kb): add SQLite schema with all 8 tables + meta version"
```

---

## Task 4: KB dataclasses

**Files:**
- Create: `src/reverser/kb/store.py` (initial — dataclasses only)
- Create: `tests/test_kb_store.py` (initial — dataclass tests only)

- [ ] **Step 1: Write failing test `tests/test_kb_store.py`**

```python
"""Tests for the KB store public API."""

from datetime import datetime
import pytest

from reverser.kb.store import HostFact, ServiceFact, CredentialFact, FindingFact


def test_host_fact_minimal():
    h = HostFact(ip="10.10.10.5")
    assert h.ip == "10.10.10.5"
    assert h.hostname is None
    assert h.is_dc is False


def test_host_fact_full():
    h = HostFact(
        ip="10.10.10.5",
        hostname="dc01",
        os="Windows Server 2019",
        domain="CORP.LOCAL",
        is_dc=True,
        smb_signing="required",
    )
    assert h.is_dc is True
    assert h.smb_signing == "required"


def test_service_fact_minimal():
    s = ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp")
    assert s.port == 445
    assert s.proto == "tcp"
    assert s.service is None


def test_credential_fact_password():
    c = CredentialFact(username="jdoe", password="Summer2026!", domain="CORP")
    assert c.password == "Summer2026!"
    assert c.nt_hash is None
    assert c.status == "untested"


def test_credential_fact_hash():
    c = CredentialFact(username="jdoe", nt_hash="aad3b4...", status="valid")
    assert c.password is None
    assert c.nt_hash == "aad3b4..."
    assert c.status == "valid"


def test_credential_fact_invalid_status_raises():
    with pytest.raises(ValueError):
        CredentialFact(username="jdoe", password="x", status="bogus")


def test_finding_fact_severity_validation():
    FindingFact(title="Test", severity="high", description="x")
    with pytest.raises(ValueError):
        FindingFact(title="Test", severity="emergency", description="x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kb_store.py -v`
Expected: errors with `ImportError`.

- [ ] **Step 3: Implement initial `src/reverser/kb/store.py` with dataclasses**

```python
"""KB public API: dataclasses + KB class for per-target SQLite access."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


_VALID_CRED_STATUS = {"untested", "invalid", "valid"}
_VALID_SEVERITY = {"info", "low", "medium", "high", "critical"}


@dataclass
class HostFact:
    ip: str
    hostname: Optional[str] = None
    os: Optional[str] = None
    domain: Optional[str] = None
    is_dc: bool = False
    smb_signing: Optional[str] = None


@dataclass
class ServiceFact:
    host_ip: str
    port: int
    proto: str
    service: Optional[str] = None
    version: Optional[str] = None
    banner: Optional[str] = None
    scan_source: Optional[str] = None


@dataclass
class CredentialFact:
    username: str
    password: Optional[str] = None
    nt_hash: Optional[str] = None
    lm_hash: Optional[str] = None
    kerberos_ticket: Optional[str] = None
    domain: Optional[str] = None
    source_tool: Optional[str] = None
    source_context: Optional[str] = None
    status: str = "untested"

    def __post_init__(self):
        if self.status not in _VALID_CRED_STATUS:
            raise ValueError(
                f"invalid credential status {self.status!r}; "
                f"must be one of {sorted(_VALID_CRED_STATUS)}"
            )


@dataclass
class FindingFact:
    title: str
    severity: str
    description: str
    evidence_paths: list[str] = field(default_factory=list)
    cvss: Optional[float] = None

    def __post_init__(self):
        if self.severity not in _VALID_SEVERITY:
            raise ValueError(
                f"invalid severity {self.severity!r}; "
                f"must be one of {sorted(_VALID_SEVERITY)}"
            )


@dataclass
class ArtifactFact:
    kind: str
    path: str
    sha256: Optional[str] = None
    source_tool: Optional[str] = None


@dataclass
class CredResult:
    service_kind: str
    target_host: str
    success: bool
    error_msg: Optional[str] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kb_store.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): add fact dataclasses with validation"
```

---

## Task 5: KB class — connection management + target normalization

**Files:**
- Modify: `src/reverser/kb/store.py` (append `KB` class skeleton)
- Modify: `tests/test_kb_store.py` (append connection tests)

- [ ] **Step 1: Write failing tests (append to `tests/test_kb_store.py`)**

```python
import os
from pathlib import Path

from reverser.kb.store import KB, normalize_target


def test_normalize_target_lowercase_strip():
    assert normalize_target("  10.10.10.5  ") == "10.10.10.5"
    assert normalize_target("DC01.CORP.LOCAL") == "dc01.corp.local"


def test_normalize_target_empty_raises():
    with pytest.raises(ValueError):
        normalize_target("")
    with pytest.raises(ValueError):
        normalize_target("   ")


def test_kb_creates_target_dir(tmp_targets_dir):
    kb = KB("10.10.10.5")
    assert (tmp_targets_dir / "10.10.10.5").is_dir()
    assert (tmp_targets_dir / "10.10.10.5" / "state.db").is_file()


def test_kb_creates_subdirs(tmp_targets_dir):
    KB("10.10.10.5")
    assert (tmp_targets_dir / "10.10.10.5" / "findings").is_dir()
    assert (tmp_targets_dir / "10.10.10.5" / "loot").is_dir()


def test_kb_target_id_normalized(tmp_targets_dir):
    kb = KB("  10.10.10.5  ")
    assert kb.target_id == "10.10.10.5"
    assert (tmp_targets_dir / "10.10.10.5").is_dir()


def test_kb_records_target_row(tmp_targets_dir):
    kb = KB("10.10.10.5")
    with kb._connect() as conn:
        row = conn.execute("SELECT id FROM targets WHERE id = ?", ("10.10.10.5",)).fetchone()
        assert row is not None
        assert row[0] == "10.10.10.5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kb_store.py -v`
Expected: 6 new failures with `ImportError` for `KB` and `normalize_target`.

- [ ] **Step 3: Append `KB` class + `normalize_target` to `src/reverser/kb/store.py`**

Append to the existing `store.py`:

```python
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import apply_schema


def normalize_target(target: str) -> str:
    """Normalize a target identifier (lowercase, strip)."""
    if not target or not target.strip():
        raise ValueError("target identifier must be non-empty")
    return target.strip().lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


class KB:
    """Per-target knowledge base. Construct via reverser.kb.for_target(target)."""

    def __init__(self, target: str):
        self.target_id = normalize_target(target)
        self.root = _targets_root() / self.target_id
        self._init_filesystem()
        self._init_database()

    def _init_filesystem(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "findings").mkdir(exist_ok=True)
        (self.root / "loot").mkdir(exist_ok=True)

    def _init_database(self) -> None:
        self.db_path = self.root / "state.db"
        with self._connect() as conn:
            apply_schema(conn)
            now = _now_iso()
            conn.execute(
                "INSERT OR IGNORE INTO targets (id, first_seen, last_active) VALUES (?, ?, ?)",
                (self.target_id, now, now),
            )
            conn.execute(
                "UPDATE targets SET last_active = ? WHERE id = ?",
                (now, self.target_id),
            )
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kb_store.py -v`
Expected: 13 passed (7 old + 6 new).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): add KB class with per-target dir and DB initialization"
```

---

## Task 6: `record_host` + idempotency

**Files:**
- Modify: `src/reverser/kb/store.py` (add `record_host` method)
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
def test_record_host_basic(kb):
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows", is_dc=True))
    hosts = kb.get_hosts()
    assert len(hosts) == 1
    assert hosts[0].ip == "10.10.10.5"
    assert hosts[0].hostname == "dc01"
    assert hosts[0].is_dc is True


def test_record_host_idempotent(kb):
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01"))
    hosts = kb.get_hosts()
    assert len(hosts) == 1
    assert hosts[0].hostname == "dc01"  # second call updates fields


def test_record_host_preserves_fields_when_none(kb):
    """Re-recording with None fields must not clobber previously-set values."""
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows"))
    kb.record_host(HostFact(ip="10.10.10.5", domain="CORP.LOCAL"))
    hosts = kb.get_hosts()
    assert hosts[0].hostname == "dc01"  # not clobbered by None
    assert hosts[0].os == "Windows"
    assert hosts[0].domain == "CORP.LOCAL"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k record_host`
Expected: 3 failures with `AttributeError: 'KB' object has no attribute 'record_host'`.

- [ ] **Step 3: Implement `record_host` and `get_hosts` in `KB`**

Add inside the `KB` class in `src/reverser/kb/store.py`:

```python
    def record_host(self, host: HostFact) -> None:
        """Insert or merge a host fact. None fields do not clobber existing values."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT hostname, os, domain, is_dc, smb_signing FROM hosts "
                "WHERE target_id = ? AND ip = ?",
                (self.target_id, host.ip),
            ).fetchone()
            now = _now_iso()
            if existing is None:
                conn.execute(
                    "INSERT INTO hosts "
                    "(target_id, ip, hostname, os, domain, is_dc, smb_signing, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.target_id, host.ip, host.hostname, host.os,
                        host.domain, int(host.is_dc), host.smb_signing, now,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE hosts SET "
                    "hostname = COALESCE(?, hostname), "
                    "os = COALESCE(?, os), "
                    "domain = COALESCE(?, domain), "
                    "is_dc = CASE WHEN ? = 1 THEN 1 ELSE is_dc END, "
                    "smb_signing = COALESCE(?, smb_signing) "
                    "WHERE target_id = ? AND ip = ?",
                    (
                        host.hostname, host.os, host.domain,
                        int(host.is_dc), host.smb_signing,
                        self.target_id, host.ip,
                    ),
                )
            conn.commit()

    def get_hosts(self) -> list[HostFact]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT ip, hostname, os, domain, is_dc, smb_signing FROM hosts "
                "WHERE target_id = ? ORDER BY ip",
                (self.target_id,),
            )
            return [
                HostFact(
                    ip=r[0], hostname=r[1], os=r[2], domain=r[3],
                    is_dc=bool(r[4]), smb_signing=r[5],
                )
                for r in cursor.fetchall()
            ]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k record_host`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): record_host with merge-on-conflict (none-preserving)"
```

---

## Task 7: `record_service` + filtered queries

**Files:**
- Modify: `src/reverser/kb/store.py`
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
def test_record_service_basic(kb):
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(
        host_ip="10.10.10.5", port=445, proto="tcp",
        service="microsoft-ds", version="Windows Server 2019",
        scan_source="nmap_scan",
    ))
    svcs = kb.get_services()
    assert len(svcs) == 1
    assert svcs[0].port == 445
    assert svcs[0].service == "microsoft-ds"


def test_record_service_idempotent(kb):
    kb.record_host(HostFact(ip="10.10.10.5"))
    s1 = ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp", service="smb")
    s2 = ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp", service="microsoft-ds", version="2019")
    kb.record_service(s1)
    kb.record_service(s2)
    svcs = kb.get_services()
    assert len(svcs) == 1
    assert svcs[0].service == "microsoft-ds"
    assert svcs[0].version == "2019"


def test_get_services_filter_by_host(kb):
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_host(HostFact(ip="10.10.10.6"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.6", port=22, proto="tcp"))
    assert len(kb.get_services(host_ip="10.10.10.5")) == 1
    assert kb.get_services(host_ip="10.10.10.5")[0].port == 445


def test_get_services_filter_by_port(kb):
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=22, proto="tcp"))
    assert len(kb.get_services(port=445)) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k service`
Expected: 4 failures.

- [ ] **Step 3: Implement `record_service` and `get_services`**

Add inside `KB` class:

```python
    def record_service(self, svc: ServiceFact) -> None:
        with self._connect() as conn:
            now = _now_iso()
            conn.execute(
                "INSERT INTO services "
                "(target_id, host_ip, port, proto, service, version, banner, scan_source, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (target_id, host_ip, port, proto) DO UPDATE SET "
                "  service = COALESCE(excluded.service, service), "
                "  version = COALESCE(excluded.version, version), "
                "  banner = COALESCE(excluded.banner, banner), "
                "  scan_source = COALESCE(excluded.scan_source, scan_source), "
                "  scanned_at = excluded.scanned_at",
                (
                    self.target_id, svc.host_ip, svc.port, svc.proto,
                    svc.service, svc.version, svc.banner, svc.scan_source, now,
                ),
            )
            conn.commit()

    def get_services(
        self, host_ip: str | None = None, port: int | None = None,
    ) -> list[ServiceFact]:
        sql = (
            "SELECT host_ip, port, proto, service, version, banner, scan_source "
            "FROM services WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if host_ip is not None:
            sql += " AND host_ip = ?"
            params.append(host_ip)
        if port is not None:
            sql += " AND port = ?"
            params.append(port)
        sql += " ORDER BY host_ip, port"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return [
                ServiceFact(
                    host_ip=r[0], port=r[1], proto=r[2],
                    service=r[3], version=r[4], banner=r[5], scan_source=r[6],
                )
                for r in cursor.fetchall()
            ]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k service`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): record_service with merge + filtered get_services"
```

---

## Task 8: `record_credential` + status transitions

**Files:**
- Modify: `src/reverser/kb/store.py`
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
def test_record_credential_new(kb):
    cred_id = kb.record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    assert cred_id > 0
    creds = kb.get_credentials()
    assert len(creds) == 1
    assert creds[0].username == "jdoe"
    assert creds[0].status == "valid"


def test_record_credential_dedup_returns_same_id(kb):
    c = CredentialFact(username="jdoe", password="x", status="untested")
    id1 = kb.record_credential(c)
    id2 = kb.record_credential(c)
    assert id1 == id2
    assert len(kb.get_credentials()) == 1


def test_record_credential_status_upgrade(kb):
    """Re-recording an existing cred with status=valid must upgrade from untested."""
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="untested"))
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    creds = kb.get_credentials()
    assert len(creds) == 1
    assert creds[0].status == "valid"


def test_record_credential_status_no_downgrade(kb):
    """Once valid, must not be downgraded to untested or invalid by a later record."""
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="invalid"))
    creds = kb.get_credentials()
    assert creds[0].status == "valid"


def test_get_credentials_filter_by_status(kb):
    kb.record_credential(CredentialFact(username="a", password="x", status="valid"))
    kb.record_credential(CredentialFact(username="b", password="y", status="invalid"))
    valid = kb.get_credentials(status="valid")
    assert len(valid) == 1
    assert valid[0].username == "a"


def test_credential_hash_distinct_from_password(kb):
    """Same user with different cred material is recorded as separate rows."""
    kb.record_credential(CredentialFact(username="jdoe", password="x"))
    kb.record_credential(CredentialFact(username="jdoe", nt_hash="aad3..."))
    assert len(kb.get_credentials()) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k credential`
Expected: 6 failures.

- [ ] **Step 3: Implement `record_credential` and `get_credentials`**

Add inside `KB` class:

```python
    _STATUS_RANK = {"untested": 0, "invalid": 1, "valid": 2}

    def record_credential(self, cred: CredentialFact) -> int:
        """Record a credential. Returns the row id.

        Behavior:
        - Dedupes on (target, username, password, nt_hash).
        - Status only moves up the ladder untested → invalid → valid (never down).
        """
        with self._connect() as conn:
            now = _now_iso()
            existing = conn.execute(
                "SELECT id, status FROM credentials WHERE "
                "target_id = ? AND username = ? AND "
                "COALESCE(password, '') = COALESCE(?, '') AND "
                "COALESCE(nt_hash, '') = COALESCE(?, '')",
                (self.target_id, cred.username, cred.password, cred.nt_hash),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    "INSERT INTO credentials "
                    "(target_id, username, password, nt_hash, lm_hash, kerberos_ticket, "
                    " domain, source_tool, source_context, status, first_seen, last_tested) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.target_id, cred.username, cred.password, cred.nt_hash,
                        cred.lm_hash, cred.kerberos_ticket, cred.domain,
                        cred.source_tool, cred.source_context, cred.status,
                        now, now if cred.status != "untested" else None,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            cred_id, current_status = existing
            new_status = (
                cred.status
                if self._STATUS_RANK[cred.status] > self._STATUS_RANK[current_status]
                else current_status
            )
            conn.execute(
                "UPDATE credentials SET status = ?, last_tested = ? WHERE id = ?",
                (new_status, now, cred_id),
            )
            conn.commit()
            return cred_id

    def get_credentials(self, status: str | None = None) -> list[CredentialFact]:
        sql = (
            "SELECT username, password, nt_hash, lm_hash, kerberos_ticket, domain, "
            "       source_tool, source_context, status "
            "FROM credentials WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return [
                CredentialFact(
                    username=r[0], password=r[1], nt_hash=r[2], lm_hash=r[3],
                    kerberos_ticket=r[4], domain=r[5],
                    source_tool=r[6], source_context=r[7], status=r[8],
                )
                for r in cursor.fetchall()
            ]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k credential`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): record_credential with dedup + monotonic status upgrades"
```

---

## Task 9: `record_cred_result` (cred-to-service mapping)

**Files:**
- Modify: `src/reverser/kb/store.py`
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
def test_record_cred_result(kb):
    cred_id = kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_cred_result(cred_id, CredResult(
        service_kind="smb", target_host="10.10.10.5", success=True,
    ))
    results = kb.get_cred_results(cred_id)
    assert len(results) == 1
    assert results[0].service_kind == "smb"
    assert results[0].success is True


def test_get_cred_results_for_cred(kb):
    cid = kb.record_credential(CredentialFact(username="jdoe", password="x"))
    kb.record_cred_result(cid, CredResult(service_kind="smb", target_host="10.10.10.5", success=True))
    kb.record_cred_result(cid, CredResult(service_kind="winrm", target_host="10.10.10.5", success=False, error_msg="STATUS_LOGON_FAILURE"))
    results = kb.get_cred_results(cid)
    assert len(results) == 2
```

- [ ] **Step 2: Import CredResult in test file (already imported in earlier tasks if not, add now)**

Ensure the import line in `tests/test_kb_store.py` is:

```python
from reverser.kb.store import HostFact, ServiceFact, CredentialFact, FindingFact, KB, normalize_target, CredResult
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k cred_result`
Expected: 2 failures.

- [ ] **Step 4: Implement `record_cred_result` + `get_cred_results`**

Add inside `KB` class:

```python
    def record_cred_result(self, cred_id: int, result: CredResult) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cred_results "
                "(cred_id, service_kind, target_host, success, error_msg, attempted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cred_id, result.service_kind, result.target_host,
                    int(result.success), result.error_msg, _now_iso(),
                ),
            )
            conn.commit()

    def get_cred_results(self, cred_id: int) -> list[CredResult]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT service_kind, target_host, success, error_msg "
                "FROM cred_results WHERE cred_id = ? ORDER BY attempted_at",
                (cred_id,),
            )
            return [
                CredResult(
                    service_kind=r[0], target_host=r[1],
                    success=bool(r[2]), error_msg=r[3],
                )
                for r in cursor.fetchall()
            ]
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k cred_result`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): record_cred_result for cred-to-service tracking"
```

---

## Task 10: `record_finding` + `record_artifact` + `record_note`

**Files:**
- Modify: `src/reverser/kb/store.py`
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
from reverser.kb.store import ArtifactFact


def test_record_finding(kb):
    fid = kb.record_finding(FindingFact(
        title="Anonymous SMB share access",
        severity="medium",
        description="The IPC$ share allows anonymous enumeration.",
        evidence_paths=["findings/smb_anon.txt"],
    ))
    assert fid > 0
    findings = kb.get_findings()
    assert len(findings) == 1
    assert findings[0].title == "Anonymous SMB share access"
    assert findings[0].severity == "medium"
    assert findings[0].evidence_paths == ["findings/smb_anon.txt"]


def test_record_finding_with_cvss(kb):
    kb.record_finding(FindingFact(
        title="CVE-2020-1472", severity="critical",
        description="Zerologon", cvss=10.0,
    ))
    f = kb.get_findings()[0]
    assert f.cvss == 10.0


def test_record_artifact(kb):
    aid = kb.record_artifact(ArtifactFact(
        kind="asreproast_hashes", path="loot/asrep_hashes.txt",
        sha256="abc123", source_tool="kerberos_enum",
    ))
    assert aid > 0
    arts = kb.get_artifacts()
    assert len(arts) == 1
    assert arts[0].kind == "asreproast_hashes"


def test_record_note(kb):
    kb.record_note("Initial recon — saw OpenSSH 7.9 on FreeBSD")
    notes = kb.get_notes()
    assert len(notes) == 1
    assert "OpenSSH" in notes[0]


def test_get_findings_filter_by_severity(kb):
    kb.record_finding(FindingFact(title="a", severity="low", description="x"))
    kb.record_finding(FindingFact(title="b", severity="high", description="x"))
    high = kb.get_findings(severity="high")
    assert len(high) == 1
    assert high[0].title == "b"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k "finding or artifact or note"`
Expected: 5 failures.

- [ ] **Step 3: Implement the three record/get pairs**

Add inside `KB` class:

```python
    def record_finding(self, finding: FindingFact) -> int:
        import json
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO findings "
                "(target_id, title, severity, cvss, description, evidence_paths, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.target_id, finding.title, finding.severity, finding.cvss,
                    finding.description, json.dumps(finding.evidence_paths), _now_iso(),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_findings(self, severity: str | None = None) -> list[FindingFact]:
        import json
        sql = (
            "SELECT title, severity, cvss, description, evidence_paths "
            "FROM findings WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        sql += " ORDER BY id"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return [
                FindingFact(
                    title=r[0], severity=r[1], cvss=r[2], description=r[3] or "",
                    evidence_paths=json.loads(r[4]) if r[4] else [],
                )
                for r in cursor.fetchall()
            ]

    def record_artifact(self, artifact: ArtifactFact) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO artifacts (target_id, kind, path, sha256, source_tool, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.target_id, artifact.kind, artifact.path,
                    artifact.sha256, artifact.source_tool, _now_iso(),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_artifacts(self) -> list[ArtifactFact]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT kind, path, sha256, source_tool FROM artifacts "
                "WHERE target_id = ? ORDER BY id",
                (self.target_id,),
            )
            return [
                ArtifactFact(kind=r[0], path=r[1], sha256=r[2], source_tool=r[3])
                for r in cursor.fetchall()
            ]

    def record_note(self, body: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO notes (target_id, body, created_at) VALUES (?, ?, ?)",
                (self.target_id, body, _now_iso()),
            )
            conn.commit()

    def get_notes(self) -> list[str]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT body FROM notes WHERE target_id = ? ORDER BY id",
                (self.target_id,),
            )
            return [r[0] for r in cursor.fetchall()]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k "finding or artifact or note"`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py
git commit -m "feat(kb): record_finding, record_artifact, record_note"
```

---

## Task 11: Public API export — `for_target()`

**Files:**
- Modify: `src/reverser/kb/__init__.py`
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
def test_for_target_returns_kb(tmp_targets_dir):
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    assert isinstance(kb, KB)
    assert kb.target_id == "10.10.10.5"


def test_for_target_caches_per_target(tmp_targets_dir):
    """Calling for_target twice with the same target should return the same instance."""
    from reverser.kb import for_target
    kb1 = for_target("10.10.10.5")
    kb2 = for_target("10.10.10.5")
    assert kb1 is kb2


def test_for_target_normalizes(tmp_targets_dir):
    from reverser.kb import for_target
    kb1 = for_target("10.10.10.5")
    kb2 = for_target("  10.10.10.5  ")
    assert kb1 is kb2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k for_target`
Expected: 3 failures with `ImportError: cannot import name 'for_target'`.

- [ ] **Step 3: Implement public API in `src/reverser/kb/__init__.py`**

Replace the contents of `src/reverser/kb/__init__.py`:

```python
"""Per-target persistent knowledge base for reverser engagements.

Public API:
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(...))
    kb.get_credentials(status="valid")
"""

from .store import (
    KB,
    HostFact,
    ServiceFact,
    CredentialFact,
    FindingFact,
    ArtifactFact,
    CredResult,
    normalize_target,
)
from .authz import require_pentest_auth, AuthorizationError

__all__ = [
    "for_target",
    "KB",
    "HostFact",
    "ServiceFact",
    "CredentialFact",
    "FindingFact",
    "ArtifactFact",
    "CredResult",
    "require_pentest_auth",
    "AuthorizationError",
    "normalize_target",
]


_kb_cache: dict[str, KB] = {}


def for_target(target: str) -> KB:
    """Return a cached KB instance for the given target.

    The instance is created on first call (with directory + DB initialization)
    and reused on subsequent calls within the same process.
    """
    target_id = normalize_target(target)
    if target_id not in _kb_cache:
        _kb_cache[target_id] = KB(target_id)
    return _kb_cache[target_id]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k for_target`
Expected: 3 passed.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: All tests in `test_kb_authz.py`, `test_kb_schema.py`, `test_kb_store.py` pass (~33 tests total).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/__init__.py tests/test_kb_store.py
git commit -m "feat(kb): expose for_target() public API with per-process cache"
```

---

## Task 12: List-targets helper

**Files:**
- Modify: `src/reverser/kb/__init__.py`
- Modify: `tests/test_kb_store.py`

- [ ] **Step 1: Append failing tests**

```python
def test_list_targets_empty(tmp_targets_dir):
    from reverser.kb import list_targets
    assert list_targets() == []


def test_list_targets_returns_existing(tmp_targets_dir):
    from reverser.kb import for_target, list_targets
    for_target("10.10.10.5")
    for_target("dc01.corp.local")
    targets = list_targets()
    assert sorted(targets) == ["10.10.10.5", "dc01.corp.local"]


def test_list_targets_ignores_non_target_dirs(tmp_targets_dir):
    from reverser.kb import for_target, list_targets
    for_target("10.10.10.5")
    # A stray dir without state.db should not be reported
    (tmp_targets_dir / "junk").mkdir()
    assert list_targets() == ["10.10.10.5"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_store.py -v -k list_targets`
Expected: 3 failures.

- [ ] **Step 3: Implement `list_targets` in `src/reverser/kb/__init__.py`**

Append to `src/reverser/kb/__init__.py`:

```python
from pathlib import Path
import os


def list_targets() -> list[str]:
    """Return all target IDs that have a state.db on disk."""
    root = Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "state.db").is_file()
    )
```

Update `__all__` list to include `"list_targets"`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_store.py -v -k list_targets`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/__init__.py tests/test_kb_store.py
git commit -m "feat(kb): list_targets() helper for target enumeration"
```

---

## Task 13: KB integration smoke test

**Files:**
- Create: `tests/test_kb_integration.py`

- [ ] **Step 1: Write the integration test**

```python
"""End-to-end smoke test exercising the full KB public API."""

from reverser.kb import (
    for_target, list_targets,
    HostFact, ServiceFact, CredentialFact, FindingFact, ArtifactFact, CredResult,
)


def test_full_engagement_flow(tmp_targets_dir):
    # Initial recon: discover host, services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows", is_dc=True))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp",
                                  service="microsoft-ds", scan_source="nmap_scan"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=389, proto="tcp",
                                  service="ldap", scan_source="nmap_scan"))

    # Spray finds a valid cred
    cred_id = kb.record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    kb.record_cred_result(cred_id, CredResult(
        service_kind="smb", target_host="10.10.10.5", success=True,
    ))

    # Same cred validated against winrm
    kb.record_cred_result(cred_id, CredResult(
        service_kind="winrm", target_host="10.10.10.5", success=True,
    ))

    # Drop a finding
    kb.record_finding(FindingFact(
        title="SMB signing not required",
        severity="medium",
        description="Allows NTLM relay attacks.",
    ))

    # Reopen the KB (simulating a new tool call) and verify state
    kb2 = for_target("10.10.10.5")
    assert kb2 is kb  # cache hit

    hosts = kb2.get_hosts()
    assert len(hosts) == 1 and hosts[0].is_dc

    services = kb2.get_services()
    assert {s.port for s in services} == {445, 389}

    valid_creds = kb2.get_credentials(status="valid")
    assert len(valid_creds) == 1 and valid_creds[0].username == "jdoe"

    results = kb2.get_cred_results(cred_id)
    assert {r.service_kind for r in results} == {"smb", "winrm"}

    findings = kb2.get_findings()
    assert len(findings) == 1

    assert "10.10.10.5" in list_targets()
```

- [ ] **Step 2: Run to verify pass**

Run: `pytest tests/test_kb_integration.py -v`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_kb_integration.py
git commit -m "test(kb): end-to-end engagement-flow integration test"
```

---

## Task 14: Final sweep — run all tests + verify clean

- [ ] **Step 1: Run the full test suite with coverage if available**

Run: `pytest -v`
Expected: All ~37 tests pass. Note the count for the next plan's regression check.

- [ ] **Step 2: Verify the public API matches the spec**

Manually check `src/reverser/kb/__init__.py` exports against the spec § "Public API":
- `for_target(target) -> KB` ✓
- `list_targets() -> list[str]` ✓
- `KB.record_host`, `record_service`, `record_credential`, `record_cred_result`, `record_finding`, `record_artifact`, `record_note` ✓
- `KB.get_hosts`, `get_services`, `get_credentials`, `get_cred_results`, `get_findings`, `get_artifacts`, `get_notes` ✓
- `require_pentest_auth`, `AuthorizationError` ✓
- All `*Fact` dataclasses ✓

- [ ] **Step 3: Final commit if any cleanup was needed**

If no changes, skip. Otherwise:

```bash
git commit -am "chore(kb): API surface verification"
```

---

## Done

Plan 1 ships a working `reverser.kb` library used by all subsequent plans. Public API is stable; further plans only consume it.

Next up: **Plan 2 — KB read-side tools + parsers + retrofits.**
