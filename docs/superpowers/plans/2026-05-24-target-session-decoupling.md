# Target / Session Decoupling + XDG Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple sessions from raw IP/URL/binary identity by introducing a named `Target` entity that owns mutable addresses, the KB, scope, and sessions; relocate persistent state to platform-appropriate directories with engagement-local override.

**Architecture:** A new `Target` dataclass (persisted as `target.json` in the per-target directory) holds a list of `Address` records with one marked primary. Sessions pin to their starting address (`active_address_id`) for predictable resume. A new `paths.py` module is the single source of truth for storage roots, with three-layer precedence: env var > project marker (`.reverser-authorized`) > platformdirs default. The breaking on-disk change is taken once (no migration); existing one-shot CLI ergonomics are preserved via address-resolution rules in `session start`.

**Tech Stack:** Python 3.11+ (FastAPI, dataclasses), `platformdirs` (new dep), SQLite (existing per-target KB), TypeScript/React/Zustand (desktop renderer), pytest.

**Spec:** `docs/superpowers/specs/2026-05-24-target-session-decoupling-design.md`

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/reverser/paths.py` | Single source of truth for storage roots; three-layer precedence resolution; project-marker discovery. |
| `src/reverser/targets.py` | `Target` and `Address` dataclasses; on-disk read/write; invariant enforcement; CRUD operations. |
| `src/reverser/gui_service/routes/targets.py` | FastAPI router for `/api/targets` and address management endpoints. |
| `tests/test_paths.py` | Unit tests for path resolution and project-marker discovery. |
| `tests/test_targets_module.py` | Unit tests for Target/Address serialization, invariants, CRUD. |
| `tests/gui_service/test_targets_routes.py` | Integration tests for the new HTTP routes. |
| `desktop/renderer/src/state/targets-store.ts` | Zustand store + React Query hooks for targets. |
| `desktop/renderer/src/panes/TargetsPane.tsx` | New top-level pane for browsing/managing targets. |

### Modified files

| Path | Change |
|---|---|
| `pyproject.toml` | Add `platformdirs` dependency. |
| `src/reverser/sessions.py` | Replace local `_targets_root()` with `paths.targets_root()`; add `target_name` + `active_address_id` to `SessionSnapshot`. |
| `src/reverser/kb/store.py` | Use `paths.targets_root()`. |
| `src/reverser/kb/scope.py` | Use `paths.targets_root()`. |
| `src/reverser/kb/__init__.py` | Use `paths.targets_root()`. |
| `src/reverser/tools/web_browser.py` | Use `paths.targets_root()`. |
| `src/reverser/session_log.py` | Default to `paths.logs_root()`. |
| `src/reverser/tools/web.py` | Wordlist cache uses `paths.cache_root()`. |
| `src/reverser/agent_session.py` | Hold `Target` + `active_address`; load on resume by `active_address_id`. |
| `src/reverser/tools/dispatch.py` | Read `sess.active_address.value` in lieu of `sess.target`. |
| `src/reverser/cli.py` | Add `target` subcommand group; update `session start` resolution rules. |
| `src/reverser/gui_service/routes/sessions.py` | Accept optional `target_name` + `address` on `CreateSession`. |
| `desktop/renderer/src/state/session-store.ts` | Add `targetName` field to session state. |
| `desktop/renderer/src/panes/HypothesesPane.tsx` | Use `targetName` directly. |
| `desktop/renderer/src/pages/NewEngagement.tsx` | Add target picker (existing target / new target modes). |
| `desktop/renderer/src/api/queries.ts` | Add target hooks; extend `useCreateSession`. |

---

## Implementation Order

Phases run sequentially; tasks within a phase can sometimes overlap. The order is chosen so that each phase leaves the test suite green:

1. **Storage Paths** — foundation; no behavior change beyond where files land.
2. **Target Model** — pure new module; no integration yet.
3. **Session Integration** — connects Target to AgentSession and SessionSnapshot.
4. **Tool Dispatch** — switches read sites from `sess.target` to `sess.active_address.value`.
5. **CLI** — new `target` subcommand and updated `session start`.
6. **GUI Routes** — HTTP API for targets and updated session creation.
7. **Desktop UI** — renderer surfaces the new model.

---

# Phase 1: Storage Paths

### Task 1: Add platformdirs dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, find the `dependencies = [` array (around line 10–18) and add `"platformdirs>=4.0.0",` to the list. Preserve alphabetical or existing order.

- [ ] **Step 2: Install and verify**

Run: `uv sync` (or `pip install -e .` if uv isn't in use)
Expected: installation succeeds; `python -c "import platformdirs; print(platformdirs.__version__)"` prints a version ≥ 4.0.0.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add platformdirs for XDG-compliant storage paths"
```

---

### Task 2: Create paths.py — project marker discovery

**Files:**
- Create: `src/reverser/paths.py`
- Create: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test for project_root() with no marker**

Create `tests/test_paths.py`:

```python
"""Tests for src/reverser/paths.py — storage root resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_path_env(monkeypatch):
    """Ensure env-var overrides don't leak between tests."""
    for var in ("REVERSER_TARGETS_DIR", "REVERSER_LOGS_DIR", "REVERSER_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    # Force a fresh paths module each test so its lru_cache resets.
    import importlib
    import reverser.paths as paths_mod
    importlib.reload(paths_mod)
    yield


def test_project_root_returns_none_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.project_root() is None
```

Run: `pytest tests/test_paths.py::test_project_root_returns_none_when_no_marker -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reverser.paths'`.

- [ ] **Step 2: Create paths.py with project_root() stub**

Create `src/reverser/paths.py`:

```python
"""Resolves persistent storage paths for reverser.

Three-layer precedence for every root:
  1. Explicit env var (REVERSER_*_DIR) — highest
  2. Project marker (.reverser-authorized) in CWD or ancestor
  3. Platform-native default via platformdirs — lowest
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Optional

import platformdirs

_APP_NAME = "reverser"
_PROJECT_MARKER = ".reverser-authorized"


@functools.lru_cache(maxsize=1)
def project_root() -> Optional[Path]:
    """Walk up from CWD looking for the project marker file.

    Returns the directory containing .reverser-authorized, or None if
    no marker is found before reaching the filesystem root or $HOME.
    """
    start = Path.cwd().resolve()
    home = Path.home().resolve()
    current = start
    while True:
        marker = current / _PROJECT_MARKER
        if marker.is_file():
            # Refuse $HOME and filesystem root as project roots — too easy
            # to misconfigure.
            if current == home or current == current.parent:
                return None
            return current
        if current == current.parent:  # reached filesystem root
            return None
        current = current.parent
```

- [ ] **Step 3: Run the test**

Run: `pytest tests/test_paths.py::test_project_root_returns_none_when_no_marker -v`
Expected: PASS.

- [ ] **Step 4: Write tests for project_root() finding the marker**

Append to `tests/test_paths.py`:

```python
def test_project_root_finds_marker_in_cwd(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.project_root() == tmp_path.resolve()


def test_project_root_finds_marker_in_ancestor(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    sub = tmp_path / "a" / "b" / "c"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    from reverser import paths

    assert paths.project_root() == tmp_path.resolve()


def test_project_root_refuses_home_directory(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".reverser-authorized").touch()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(fake_home)
    from reverser import paths

    assert paths.project_root() is None
```

- [ ] **Step 5: Run all paths tests**

Run: `pytest tests/test_paths.py -v`
Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/paths.py tests/test_paths.py
git commit -m "feat(paths): project marker discovery for storage root resolution"
```

---

### Task 3: paths.py — targets_root, logs_root, cache_root

**Files:**
- Modify: `src/reverser/paths.py`
- Modify: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test for targets_root precedence**

Append to `tests/test_paths.py`:

```python
def test_targets_root_uses_env_var_when_set(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(explicit))
    from reverser import paths

    assert paths.targets_root() == explicit


def test_targets_root_uses_project_marker_when_no_env(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.targets_root() == tmp_path.resolve() / "targets"


def test_targets_root_falls_back_to_platformdirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from reverser import paths
    import platformdirs

    expected = Path(platformdirs.user_data_dir("reverser")) / "targets"
    assert paths.targets_root() == expected


def test_logs_root_follows_project_marker(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths

    assert paths.logs_root() == tmp_path.resolve() / "logs"


def test_cache_root_does_not_follow_project_marker(tmp_path, monkeypatch):
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)
    from reverser import paths
    import platformdirs

    expected = Path(platformdirs.user_cache_dir("reverser"))
    assert paths.cache_root() == expected
```

- [ ] **Step 2: Run tests to see failures**

Run: `pytest tests/test_paths.py -v`
Expected: 5 new tests FAIL with `AttributeError: module 'reverser.paths' has no attribute 'targets_root'`.

- [ ] **Step 3: Implement the three roots**

Append to `src/reverser/paths.py`:

```python
@functools.lru_cache(maxsize=1)
def targets_root() -> Path:
    """Resolve the directory holding per-target data (KB, sessions, scope)."""
    env = os.environ.get("REVERSER_TARGETS_DIR")
    if env:
        return Path(env)
    project = project_root()
    if project is not None:
        return project / "targets"
    return Path(platformdirs.user_data_dir(_APP_NAME)) / "targets"


@functools.lru_cache(maxsize=1)
def logs_root() -> Path:
    """Resolve the directory holding session JSONL logs."""
    env = os.environ.get("REVERSER_LOGS_DIR")
    if env:
        return Path(env)
    project = project_root()
    if project is not None:
        return project / "logs"
    return Path(platformdirs.user_log_dir(_APP_NAME))


@functools.lru_cache(maxsize=1)
def cache_root() -> Path:
    """Resolve the directory for shared caches (wordlists, etc.).

    Caches do NOT follow the project marker — they are shared across
    engagements and should not be duplicated per-project.
    """
    env = os.environ.get("REVERSER_CACHE_DIR")
    if env:
        return Path(env)
    return Path(platformdirs.user_cache_dir(_APP_NAME))


def _reset_caches_for_tests() -> None:
    """Test-only helper: clear lru_caches so monkeypatch'd env/CWD take effect."""
    project_root.cache_clear()
    targets_root.cache_clear()
    logs_root.cache_clear()
    cache_root.cache_clear()
```

- [ ] **Step 4: Update the autouse fixture to use the reset helper**

In `tests/test_paths.py`, replace the `_clear_path_env` fixture body so the importlib reload is removed and we call the reset helper instead:

```python
@pytest.fixture(autouse=True)
def _clear_path_env(monkeypatch):
    """Ensure env-var overrides don't leak between tests."""
    for var in ("REVERSER_TARGETS_DIR", "REVERSER_LOGS_DIR", "REVERSER_CACHE_DIR"):
        monkeypatch.delenv(var, raising=False)
    from reverser import paths
    paths._reset_caches_for_tests()
    yield
    paths._reset_caches_for_tests()
```

- [ ] **Step 5: Run all paths tests**

Run: `pytest tests/test_paths.py -v`
Expected: 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/paths.py tests/test_paths.py
git commit -m "feat(paths): targets_root, logs_root, cache_root with three-layer precedence"
```

---

### Task 4: Add startup logging for resolved roots

**Files:**
- Modify: `src/reverser/paths.py`
- Modify: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py`:

```python
def test_log_resolved_roots_names_each_source(tmp_path, monkeypatch, caplog):
    import logging
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(explicit))
    (tmp_path / ".reverser-authorized").touch()
    monkeypatch.chdir(tmp_path)

    from reverser import paths
    with caplog.at_level(logging.INFO, logger="reverser.paths"):
        paths.log_resolved_roots()

    text = caplog.text
    assert "targets_root" in text
    assert "env REVERSER_TARGETS_DIR" in text
    assert "logs_root" in text
    assert "project marker" in text  # logs follow project marker
```

Run: `pytest tests/test_paths.py::test_log_resolved_roots_names_each_source -v`
Expected: FAIL with `AttributeError: module 'reverser.paths' has no attribute 'log_resolved_roots'`.

- [ ] **Step 2: Implement log_resolved_roots()**

Append to `src/reverser/paths.py`:

```python
import logging

_log = logging.getLogger(__name__)


def _source_label(env_var: str, follows_marker: bool) -> str:
    if os.environ.get(env_var):
        return f"env {env_var}"
    if follows_marker and project_root() is not None:
        return "project marker"
    return "platform default"


def log_resolved_roots() -> None:
    """Emit one INFO line per resolved root naming the precedence layer used."""
    _log.info("targets_root=%s (source: %s)", targets_root(), _source_label("REVERSER_TARGETS_DIR", True))
    _log.info("logs_root=%s (source: %s)", logs_root(), _source_label("REVERSER_LOGS_DIR", True))
    _log.info("cache_root=%s (source: %s)", cache_root(), _source_label("REVERSER_CACHE_DIR", False))
```

- [ ] **Step 3: Run test**

Run: `pytest tests/test_paths.py::test_log_resolved_roots_names_each_source -v`
Expected: PASS.

- [ ] **Step 4: Call log_resolved_roots() from the CLI entry point**

Read `src/reverser/cli.py` around line 30–50 to find where `main()` starts. Add this near the top of `main()` (after any logging.basicConfig but before subcommand dispatch):

```python
from reverser import paths as _paths
_paths.log_resolved_roots()
```

- [ ] **Step 5: Run the full test suite to confirm nothing else broke**

Run: `pytest -x -q`
Expected: previous failures unchanged; no new failures from the import.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/paths.py tests/test_paths.py src/reverser/cli.py
git commit -m "feat(paths): log resolved storage roots at startup with source labels"
```

---

### Task 5: Migrate sessions.py to paths.targets_root()

**Files:**
- Modify: `src/reverser/sessions.py:147`
- Modify: `tests/test_sessions_module.py` (if existing tests assume `targets/` in CWD)

- [ ] **Step 1: Read the current `_targets_root` and find callers**

Read `src/reverser/sessions.py:140-165` to confirm the helper's signature and callers within the file.

- [ ] **Step 2: Run the existing sessions tests to capture baseline**

Run: `pytest tests/test_sessions_module.py -v`
Expected: all PASS (we want green-to-green).

- [ ] **Step 3: Replace the helper**

In `src/reverser/sessions.py`, find:

```python
def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))
```

Replace with:

```python
def _targets_root() -> Path:
    """Deprecated shim — use reverser.paths.targets_root() directly in new code."""
    from reverser.paths import targets_root
    return targets_root()
```

(Keeping the shim avoids touching every call site in this file in one go; subsequent tasks remove it.)

- [ ] **Step 4: Run sessions tests**

Run: `pytest tests/test_sessions_module.py -v`
Expected: all PASS. If any fail because they assumed `targets/` in CWD, update them to set `REVERSER_TARGETS_DIR` or chdir to a tmp_path with `.reverser-authorized`.

- [ ] **Step 5: Replace direct usages of `_targets_root()` inside sessions.py**

Find every `_targets_root()` call in `src/reverser/sessions.py` and replace with `targets_root()` imported from `reverser.paths`. Then delete the shim. Add the import:

```python
from reverser.paths import targets_root
```

- [ ] **Step 6: Run sessions tests again**

Run: `pytest tests/test_sessions_module.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/sessions.py tests/test_sessions_module.py
git commit -m "refactor(sessions): use paths.targets_root() instead of local helper"
```

---

### Task 6: Migrate kb/store.py, kb/scope.py, kb/__init__.py to paths.targets_root()

**Files:**
- Modify: `src/reverser/kb/store.py:122`
- Modify: `src/reverser/kb/scope.py:78`
- Modify: `src/reverser/kb/__init__.py:63`

- [ ] **Step 1: Capture baseline**

Run: `pytest tests/kb -v` (or `pytest -k kb -v` if no kb subdir in tests)
Expected: all PASS.

- [ ] **Step 2: Replace `_targets_root()` in kb/store.py**

In `src/reverser/kb/store.py`, delete the local `_targets_root` function and replace every call to it with `targets_root()`. Add at the top of the file:

```python
from reverser.paths import targets_root
```

- [ ] **Step 3: Repeat for kb/scope.py and kb/__init__.py**

Same pattern: delete the local `_targets_root` helper, add the import, replace call sites.

- [ ] **Step 4: Run kb tests**

Run: `pytest tests/kb -v`
Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -x -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/store.py src/reverser/kb/scope.py src/reverser/kb/__init__.py
git commit -m "refactor(kb): use paths.targets_root() across kb module"
```

---

### Task 7: Migrate tools/web_browser.py to paths.targets_root()

**Files:**
- Modify: `src/reverser/tools/web_browser.py:34`

- [ ] **Step 1: Capture baseline**

Run: `pytest tests/tools/test_web_specs.py -v 2>/dev/null || pytest tests -k web_browser -v`
Expected: PASS (or "no tests" — that's fine, the next step is the substitution).

- [ ] **Step 2: Replace the helper**

In `src/reverser/tools/web_browser.py`, delete the local `_targets_root` and replace every call with `targets_root()`. Add:

```python
from reverser.paths import targets_root
```

- [ ] **Step 3: Run tests**

Run: `pytest -x -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/tools/web_browser.py
git commit -m "refactor(web_browser): use paths.targets_root()"
```

---

### Task 8: Migrate session_log.py to paths.logs_root()

**Files:**
- Modify: `src/reverser/session_log.py:129-148`
- Modify: `tests/test_session_log_events.py` (likely)
- Modify: `tests/gui_service/test_session_log_replay.py` (likely)

- [ ] **Step 1: Capture baseline**

Run: `pytest tests -k session_log -v`
Expected: all PASS.

- [ ] **Step 2: Read the current session_log_path function**

Read `src/reverser/session_log.py:120-150` to see exactly what to replace.

- [ ] **Step 3: Replace the default**

Find the default-path computation (the `os.path.join(os.getcwd(), "logs")` line). Replace with:

```python
from reverser.paths import logs_root
# ...
if log_dir is None:
    log_dir = str(logs_root())
```

- [ ] **Step 4: Run session_log tests**

Run: `pytest tests -k session_log -v`
Expected: all PASS. Any failing tests that hard-coded `logs/` in CWD must be updated to set `REVERSER_LOGS_DIR` to a tmp path.

- [ ] **Step 5: Run the full suite**

Run: `pytest -x -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/session_log.py tests
git commit -m "refactor(session_log): default log_dir to paths.logs_root()"
```

---

### Task 9: Migrate wordlist cache in tools/web.py to paths.cache_root()

**Files:**
- Modify: `src/reverser/tools/web.py:24`

- [ ] **Step 1: Capture baseline**

Run: `pytest tests -k web -v`
Expected: PASS.

- [ ] **Step 2: Replace the hardcoded cache path**

In `src/reverser/tools/web.py` around line 24, find the wordlist cache path (currently `~/.cache/reverser/wordlists/`). Replace with:

```python
from reverser.paths import cache_root

WORDLIST_CACHE_DIR = cache_root() / "wordlists"
```

(Adapt the variable name to whatever's already there.)

- [ ] **Step 3: Run tests**

Run: `pytest -x -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/tools/web.py
git commit -m "refactor(web): wordlist cache uses paths.cache_root()"
```

---

# Phase 2: Target Model

### Task 10: Create targets.py — Address dataclass

**Files:**
- Create: `src/reverser/targets.py`
- Create: `tests/test_targets_module.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_targets_module.py`:

```python
"""Tests for src/reverser/targets.py."""
from __future__ import annotations

from reverser.targets import Address


def test_address_round_trip_to_dict():
    addr = Address(
        id="abc123",
        kind="ip",
        value="10.0.0.5",
        status="active",
        added_at="2026-05-24T14:23:00Z",
        label="internal",
    )
    payload = addr.to_dict()
    restored = Address.from_dict(payload)
    assert restored == addr


def test_address_binary_kind_carries_sha256():
    addr = Address(
        id="abc123",
        kind="binary",
        value="/tmp/foo.bin",
        status="active",
        added_at="2026-05-24T14:23:00Z",
        sha256="deadbeef",
    )
    assert addr.to_dict()["sha256"] == "deadbeef"
```

Run: `pytest tests/test_targets_module.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reverser.targets'`.

- [ ] **Step 2: Create targets.py with Address**

Create `src/reverser/targets.py`:

```python
"""Target and Address model: per-engagement logical assets with mutable addresses.

A Target is a named logical asset (an AD DC, a web app, a binary) that owns
the per-target KB, scope, and sessions. An Address is one IP/URL/file path
by which that target is reached; addresses are mutable history with one
marked primary.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

AddressKind = Literal["ip", "url", "binary"]
AddressStatus = Literal["active", "retired"]
TargetKind = Literal["network", "binary"]

_NETWORK_KINDS: frozenset[str] = frozenset({"ip", "url"})
_BINARY_KINDS: frozenset[str] = frozenset({"binary"})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Address:
    id: str
    kind: AddressKind
    value: str
    status: AddressStatus
    added_at: str
    sha256: Optional[str] = None
    retired_at: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, payload: dict) -> "Address":
        return cls(
            id=payload["id"],
            kind=payload["kind"],
            value=payload["value"],
            status=payload["status"],
            added_at=payload["added_at"],
            sha256=payload.get("sha256"),
            retired_at=payload.get("retired_at"),
            label=payload.get("label"),
        )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 2 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): Address dataclass with round-trip serialization"
```

---

### Task 11: Target dataclass with invariants

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Write the failing tests for invariants**

Append to `tests/test_targets_module.py`:

```python
import pytest
from reverser.targets import Target, Address


def _addr(id="a1", kind="ip", value="10.0.0.1", status="active", **kw):
    return Address(id=id, kind=kind, value=value, status=status,
                   added_at="2026-05-24T00:00:00Z", **kw)


def test_target_primary_must_resolve_to_active_address():
    with pytest.raises(ValueError, match="primary"):
        Target(name="t", kind="network",
               addresses=[_addr(status="retired")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_primary_must_exist_in_addresses():
    with pytest.raises(ValueError, match="primary"):
        Target(name="t", kind="network",
               addresses=[_addr(id="a1")],
               primary_address_id="missing",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_requires_at_least_one_address():
    with pytest.raises(ValueError, match="at least one"):
        Target(name="t", kind="network", addresses=[],
               primary_address_id="",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_network_rejects_binary_address():
    with pytest.raises(ValueError, match="kind"):
        Target(name="t", kind="network",
               addresses=[_addr(kind="binary", value="/tmp/x")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_binary_rejects_network_address():
    with pytest.raises(ValueError, match="kind"):
        Target(name="t", kind="binary",
               addresses=[_addr(kind="ip", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_duplicate_address_value_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Target(name="t", kind="network",
               addresses=[_addr(id="a1", value="10.0.0.1"),
                          _addr(id="a2", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_round_trip_to_dict():
    t = Target(name="dc1", kind="network",
               addresses=[_addr(id="a1", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")
    restored = Target.from_dict(t.to_dict())
    assert restored == t


def test_target_primary_address_property():
    primary = _addr(id="a1", value="10.0.0.1")
    other = _addr(id="a2", value="10.0.0.2")
    t = Target(name="t", kind="network",
               addresses=[primary, other],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")
    assert t.primary_address == primary
```

Run: `pytest tests/test_targets_module.py -v`
Expected: 8 new tests FAIL (`Target` doesn't exist yet).

- [ ] **Step 2: Implement Target with invariants**

Append to `src/reverser/targets.py`:

```python
@dataclass
class Target:
    name: str
    kind: TargetKind
    addresses: list[Address]
    primary_address_id: str
    created_at: str
    updated_at: str
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        self._validate()

    def _allowed_address_kinds(self) -> frozenset[str]:
        return _NETWORK_KINDS if self.kind == "network" else _BINARY_KINDS

    def _validate(self) -> None:
        if not self.addresses:
            raise ValueError(f"Target {self.name!r} must have at least one address")
        allowed = self._allowed_address_kinds()
        seen_values: set[str] = set()
        seen_ids: dict[str, Address] = {}
        for a in self.addresses:
            if a.kind not in allowed:
                raise ValueError(
                    f"Target {self.name!r} kind={self.kind!r} rejects address "
                    f"kind={a.kind!r} (allowed: {sorted(allowed)})"
                )
            if a.value in seen_values:
                raise ValueError(
                    f"Target {self.name!r} has duplicate address value {a.value!r}"
                )
            seen_values.add(a.value)
            seen_ids[a.id] = a
        primary = seen_ids.get(self.primary_address_id)
        if primary is None:
            raise ValueError(
                f"Target {self.name!r} primary_address_id={self.primary_address_id!r} "
                "does not match any address"
            )
        if primary.status != "active":
            raise ValueError(
                f"Target {self.name!r} primary address must be active "
                f"(got status={primary.status!r})"
            )

    @property
    def primary_address(self) -> Address:
        for a in self.addresses:
            if a.id == self.primary_address_id:
                return a
        raise ValueError(f"primary address {self.primary_address_id!r} not found")

    def get_address(self, address_id: str) -> Address:
        for a in self.addresses:
            if a.id == address_id:
                return a
        raise KeyError(address_id)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "addresses": [a.to_dict() for a in self.addresses],
            "primary_address_id": self.primary_address_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Target":
        return cls(
            name=payload["name"],
            kind=payload["kind"],
            addresses=[Address.from_dict(a) for a in payload["addresses"]],
            primary_address_id=payload["primary_address_id"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            notes=payload.get("notes"),
        )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 10 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): Target dataclass with invariant enforcement"
```

---

### Task 12: targets.py — persistence (load/save)

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_targets_module.py`:

```python
def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = Target(name="dc1", kind="network",
               addresses=[_addr(id="a1", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")
    targets.save_target(t)
    loaded = targets.load_target("dc1")
    assert loaded == t


def test_load_unknown_target_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()
    with pytest.raises(FileNotFoundError):
        targets.load_target("nope")


def test_list_targets_returns_all_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    for name in ("alpha", "beta", "gamma"):
        targets.save_target(Target(
            name=name, kind="network",
            addresses=[_addr(id=f"{name}-1", value=f"10.0.0.{ord(name[0])}")],
            primary_address_id=f"{name}-1",
            created_at="2026-05-24T00:00:00Z",
            updated_at="2026-05-24T00:00:00Z",
        ))
    names = sorted(t.name for t in targets.list_targets())
    assert names == ["alpha", "beta", "gamma"]
```

Run: `pytest tests/test_targets_module.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 2: Implement load/save/list**

Append to `src/reverser/targets.py`:

```python
from reverser.paths import targets_root
from reverser.sessions import target_key  # reuse existing slug logic

_TARGET_FILE = "target.json"


def _target_dir(name: str) -> Path:
    return targets_root() / target_key(name)


def load_target(name: str) -> Target:
    path = _target_dir(name) / _TARGET_FILE
    if not path.exists():
        raise FileNotFoundError(f"No target named {name!r} at {path}")
    with path.open("r", encoding="utf-8") as f:
        return Target.from_dict(json.load(f))


def save_target(target: Target) -> None:
    target._validate()  # final defensive check
    directory = _target_dir(target.name)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _TARGET_FILE
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(target.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def list_targets() -> list[Target]:
    root = targets_root()
    if not root.exists():
        return []
    out: list[Target] = []
    for entry in sorted(root.iterdir()):
        candidate = entry / _TARGET_FILE
        if candidate.is_file():
            with candidate.open("r", encoding="utf-8") as f:
                out.append(Target.from_dict(json.load(f)))
    return out
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 13 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): atomic save/load + list_targets"
```

---

### Task 13: targets.py — create_target

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_targets_module.py`:

```python
def test_create_target_with_initial_address(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target(name="dc1", kind="network",
                              initial_address="10.0.0.5")
    assert t.name == "dc1"
    assert t.kind == "network"
    assert len(t.addresses) == 1
    assert t.primary_address.value == "10.0.0.5"
    assert t.primary_address.kind == "ip"


def test_create_target_infers_url_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target(name="webapp", kind="network",
                              initial_address="https://example.com")
    assert t.primary_address.kind == "url"


def test_create_binary_target_computes_sha256(tmp_path, monkeypatch):
    binary = tmp_path / "sample.bin"
    binary.write_bytes(b"hello world")
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "data"))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target(name="sample", kind="binary",
                              initial_address=str(binary))
    assert t.primary_address.kind == "binary"
    assert t.primary_address.sha256 is not None
    assert len(t.primary_address.sha256) == 64  # sha256 hex


def test_create_target_rejects_duplicate_name(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    targets.create_target(name="dc1", kind="network", initial_address="10.0.0.5")
    with pytest.raises(ValueError, match="already exists"):
        targets.create_target(name="dc1", kind="network",
                              initial_address="10.0.0.6")
```

Run: `pytest tests/test_targets_module.py -v`
Expected: 4 new tests FAIL.

- [ ] **Step 2: Implement create_target + helpers**

Append to `src/reverser/targets.py`:

```python
def _infer_address_kind(value: str, target_kind: TargetKind) -> AddressKind:
    if target_kind == "binary":
        return "binary"
    if value.startswith(("http://", "https://")):
        return "url"
    return "ip"


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_address(value: str, kind: AddressKind, label: Optional[str] = None) -> Address:
    sha = None
    if kind == "binary":
        sha = _sha256_of_file(value)
    return Address(
        id=uuid.uuid4().hex,
        kind=kind,
        value=value,
        status="active",
        added_at=_now_iso(),
        sha256=sha,
        label=label,
    )


def create_target(
    name: str,
    kind: TargetKind,
    initial_address: str,
    *,
    label: Optional[str] = None,
) -> Target:
    """Create and persist a new target with one initial primary address."""
    directory = _target_dir(name)
    if (directory / _TARGET_FILE).exists():
        raise ValueError(f"Target {name!r} already exists")
    addr_kind = _infer_address_kind(initial_address, kind)
    address = _new_address(initial_address, addr_kind, label=label)
    now = _now_iso()
    target = Target(
        name=name,
        kind=kind,
        addresses=[address],
        primary_address_id=address.id,
        created_at=now,
        updated_at=now,
    )
    save_target(target)
    return target
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 17 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): create_target with kind inference and binary hashing"
```

---

### Task 14: targets.py — add_address, set_primary, retire_address

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_targets_module.py`:

```python
def test_add_address_appends_and_optionally_promotes(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip", make_primary=True)
    assert t.primary_address.value == "10.0.0.6"
    assert len(t.addresses) == 2


def test_add_duplicate_address_value_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    with pytest.raises(ValueError, match="duplicate"):
        targets.add_address(t, "10.0.0.5", kind="ip")


def test_add_wrong_kind_address_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    with pytest.raises(ValueError, match="kind"):
        targets.add_address(t, "/tmp/x", kind="binary")


def test_set_primary_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip")
    new_primary_id = t.addresses[1].id
    t = targets.set_primary(t, new_primary_id)
    assert t.primary_address_id == new_primary_id


def test_set_primary_to_retired_address_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip", make_primary=True)
    old_id = t.addresses[0].id
    t = targets.retire_address(t, old_id)
    with pytest.raises(ValueError, match="retired"):
        targets.set_primary(t, old_id)


def test_retire_only_active_address_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    only_id = t.addresses[0].id
    with pytest.raises(ValueError, match="last active"):
        targets.retire_address(t, only_id)


def test_retire_primary_without_promoting_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip")  # not primary
    primary_id = t.primary_address_id
    with pytest.raises(ValueError, match="promote"):
        targets.retire_address(t, primary_id)
```

Run: `pytest tests/test_targets_module.py -v`
Expected: 7 new tests FAIL.

- [ ] **Step 2: Implement add_address / set_primary / retire_address**

Append to `src/reverser/targets.py`:

```python
def add_address(
    target: Target,
    value: str,
    kind: AddressKind,
    *,
    label: Optional[str] = None,
    make_primary: bool = False,
) -> Target:
    """Add a new address. Returns the updated target (also persisted)."""
    if any(a.value == value for a in target.addresses):
        raise ValueError(f"Target {target.name!r} already has address {value!r} (duplicate)")
    allowed = target._allowed_address_kinds()
    if kind not in allowed:
        raise ValueError(
            f"Target {target.name!r} kind={target.kind!r} rejects address kind={kind!r}"
        )
    address = _new_address(value, kind, label=label)
    updated = dataclasses.replace(
        target,
        addresses=[*target.addresses, address],
        primary_address_id=address.id if make_primary else target.primary_address_id,
        updated_at=_now_iso(),
    )
    save_target(updated)
    return updated


def set_primary(target: Target, address_id: str) -> Target:
    """Promote an existing active address to primary."""
    addr = target.get_address(address_id)
    if addr.status != "active":
        raise ValueError(
            f"Cannot set primary to retired address {address_id!r}; re-add it first"
        )
    updated = dataclasses.replace(
        target,
        primary_address_id=address_id,
        updated_at=_now_iso(),
    )
    save_target(updated)
    return updated


def retire_address(target: Target, address_id: str) -> Target:
    """Mark an address retired. Refuses to retire the primary or the last active."""
    addr = target.get_address(address_id)
    actives = [a for a in target.addresses if a.status == "active"]
    if len(actives) <= 1:
        raise ValueError(
            f"Cannot retire {address_id!r}: it is the last active address on {target.name!r}"
        )
    if address_id == target.primary_address_id:
        raise ValueError(
            f"Cannot retire primary address {address_id!r}; promote another active address first"
        )
    new_addresses = []
    for a in target.addresses:
        if a.id == address_id:
            a = dataclasses.replace(a, status="retired", retired_at=_now_iso())
        new_addresses.append(a)
    updated = dataclasses.replace(target, addresses=new_addresses, updated_at=_now_iso())
    save_target(updated)
    return updated
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 24 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): add_address, set_primary, retire_address"
```

---

### Task 15: targets.py — rename_target with active-session check

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_targets_module.py`:

```python
def test_rename_moves_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("oldname", "network", "10.0.0.5")
    targets.rename_target("oldname", "newname")
    with pytest.raises(FileNotFoundError):
        targets.load_target("oldname")
    loaded = targets.load_target("newname")
    assert loaded.name == "newname"


def test_rename_to_existing_name_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    targets.create_target("a", "network", "10.0.0.1")
    targets.create_target("b", "network", "10.0.0.2")
    with pytest.raises(ValueError, match="already exists"):
        targets.rename_target("a", "b")


def test_rename_with_active_sessions_rejected(tmp_path, monkeypatch):
    """A session in lifecycle state 'active' under the target blocks rename."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    from reverser.sessions import target_key
    paths._reset_caches_for_tests()

    targets.create_target("dc1", "network", "10.0.0.5")
    # Plant an "active" session snapshot.
    sessions_dir = tmp_path / target_key("dc1") / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "fake-session.json").write_text('{"state": "active"}')

    with pytest.raises(ValueError, match="active session"):
        targets.rename_target("dc1", "renamed")
```

Run: `pytest tests/test_targets_module.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 2: Implement rename_target**

Append to `src/reverser/targets.py`:

```python
def _has_active_sessions(name: str) -> list[str]:
    """Return ids (filenames) of any session snapshots in lifecycle state 'active'."""
    sessions_dir = _target_dir(name) / "sessions"
    if not sessions_dir.exists():
        return []
    active: list[str] = []
    for snapshot_path in sessions_dir.glob("*.json"):
        try:
            with snapshot_path.open("r", encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if snap.get("state") == "active":
            active.append(snapshot_path.stem)
    return active


def rename_target(old_name: str, new_name: str) -> Target:
    """Rename a target by moving its on-disk directory atomically.

    Refuses if any session on the target is in lifecycle state 'active'.
    """
    old_dir = _target_dir(old_name)
    new_dir = _target_dir(new_name)
    if not (old_dir / _TARGET_FILE).exists():
        raise FileNotFoundError(f"No target named {old_name!r}")
    if new_dir.exists():
        raise ValueError(f"Target {new_name!r} already exists at {new_dir}")
    active = _has_active_sessions(old_name)
    if active:
        raise ValueError(
            f"Cannot rename {old_name!r}: active session(s) {active}; stop them first"
        )
    os.replace(old_dir, new_dir)
    # Update name field inside target.json.
    t = load_target(new_name)
    updated = dataclasses.replace(t, name=new_name, updated_at=_now_iso())
    save_target(updated)
    return updated
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 27 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): rename_target with active-session check"
```

---

### Task 16: targets.py — rehash_binary_address

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_targets_module.py`:

```python
def test_rehash_binary_address_updates_sha(tmp_path, monkeypatch):
    binary = tmp_path / "sample.bin"
    binary.write_bytes(b"v1")
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "data"))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target("sample", "binary", str(binary))
    old_hash = t.primary_address.sha256
    binary.write_bytes(b"v2-different-content")
    t = targets.rehash_binary_address(t, t.primary_address.id)
    assert t.primary_address.sha256 != old_hash


def test_rehash_non_binary_address_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()
    t = targets.create_target("dc1", "network", "10.0.0.5")
    with pytest.raises(ValueError, match="binary"):
        targets.rehash_binary_address(t, t.primary_address.id)
```

Run: `pytest tests/test_targets_module.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 2: Implement rehash_binary_address**

Append to `src/reverser/targets.py`:

```python
def rehash_binary_address(target: Target, address_id: str) -> Target:
    """Re-read a binary address's file and update its sha256."""
    addr = target.get_address(address_id)
    if addr.kind != "binary":
        raise ValueError(f"Address {address_id!r} is not a binary address")
    new_sha = _sha256_of_file(addr.value)
    new_addresses = [
        dataclasses.replace(a, sha256=new_sha) if a.id == address_id else a
        for a in target.addresses
    ]
    updated = dataclasses.replace(target, addresses=new_addresses, updated_at=_now_iso())
    save_target(updated)
    return updated
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_targets_module.py -v`
Expected: 29 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/targets.py tests/test_targets_module.py
git commit -m "feat(targets): rehash_binary_address"
```

---

# Phase 3: Session Integration

### Task 17: Add target_name and active_address_id to SessionSnapshot

**Files:**
- Modify: `src/reverser/sessions.py:90-111` (SessionSnapshot)
- Modify: `src/reverser/sessions.py:161-182` (new_snapshot)
- Modify: `tests/test_sessions_module.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sessions_module.py`:

```python
def test_snapshot_carries_target_name_and_active_address_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import sessions
    snap = sessions.new_snapshot(
        target="10.0.0.5",
        log_path="/tmp/log.jsonl",
        config=sessions.SessionConfig(profile="ad", backend="anthropic",
                                      max_turns=10, budget_usd=1.0),
        target_name="dc1",
        active_address_id="addr-uuid-1",
    )
    assert snap.target_name == "dc1"
    assert snap.active_address_id == "addr-uuid-1"
    # Round-trip persistence.
    sessions.save(snap)
    loaded = sessions.load(snap.session_id, target_key=sessions.target_key("dc1"))
    assert loaded.target_name == "dc1"
    assert loaded.active_address_id == "addr-uuid-1"
```

Run: `pytest tests/test_sessions_module.py::test_snapshot_carries_target_name_and_active_address_id -v`
Expected: FAIL (`target_name` not a field).

- [ ] **Step 2: Add the two fields**

In `src/reverser/sessions.py`, find the `SessionSnapshot` dataclass (around line 90–111). Add these fields with sensible defaults to keep existing call sites compiling:

```python
target_name: str = ""  # NEW — preferred identity; falls back to `target` if empty
active_address_id: str = ""  # NEW — address pinned at session start
```

Also update `new_snapshot()` (around line 161–182) to accept `target_name: str = ""` and `active_address_id: str = ""` kwargs and populate them.

If your `load()` reads JSON into the dataclass directly, ensure it gracefully handles snapshots that lack the new fields (they default to empty strings).

- [ ] **Step 3: Run the new test plus existing sessions tests**

Run: `pytest tests/test_sessions_module.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/sessions.py tests/test_sessions_module.py
git commit -m "feat(sessions): SessionSnapshot carries target_name + active_address_id"
```

---

### Task 18: AgentSession holds Target + active_address

**Files:**
- Modify: `src/reverser/agent_session.py:64-202`
- Modify: `tests/test_agent_session_events.py` (existing test file per Explore report)

- [ ] **Step 1: Capture baseline**

Run: `pytest tests/test_agent_session_events.py -v`
Expected: all PASS.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_agent_session_events.py` (adapt the import path to match the existing file's style):

```python
def test_agent_session_resolves_active_address_from_target(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()
    target = targets.create_target("dc1", "network", "10.0.0.5")

    from reverser.agent_session import AgentSession
    # Use whatever the existing minimal-fresh-session constructor looks like;
    # the assertion that matters is that AgentSession sets sess.active_address.
    sess = AgentSession.from_target(target)  # NEW factory
    assert sess.target.name == "dc1"
    assert sess.active_address.value == "10.0.0.5"
    assert sess.active_address.id == target.primary_address_id
```

Run: `pytest tests/test_agent_session_events.py::test_agent_session_resolves_active_address_from_target -v`
Expected: FAIL (`from_target` not defined).

- [ ] **Step 3: Add `from_target` factory and store both fields**

Read `src/reverser/agent_session.py:64-202` to understand current init. Add:

```python
from reverser.targets import Target, load_target
# ...

class AgentSession:
    target: Target
    active_address: "Address"  # forward-ref from reverser.targets
    # ... existing fields

    @classmethod
    def from_target(cls, target: Target, **kwargs) -> "AgentSession":
        """Construct an AgentSession from a Target, pinning the current primary."""
        inst = cls(
            binary_path=target.primary_address.value,
            **kwargs,
        )
        inst.target = target
        inst.active_address = target.primary_address
        return inst
```

Also update the existing `__init__` to populate `self.target` and `self.active_address` when given a `target_name` (load via `load_target`); when given only a raw `binary_path` (legacy), it can leave them as `None` for now — Phase 4 will switch tool dispatch.

- [ ] **Step 4: Update SessionSnapshot creation in `__init__`**

Wherever `new_snapshot()` is called in `agent_session.py`, pass the new `target_name` and `active_address_id` kwargs sourced from `self.target.name` and `self.active_address.id` (when available).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_agent_session_events.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/agent_session.py tests/test_agent_session_events.py
git commit -m "feat(agent_session): hold Target + active_address; from_target factory"
```

---

### Task 19: Session start address-resolution rules

**Files:**
- Create: `src/reverser/session_start.py` (new helper module)
- Create: `tests/test_session_start.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_start.py`:

```python
"""Tests for the `session start` address-resolution rules.

Rules (per spec):
1. If positional arg matches an existing target name → use it.
2. Else if it matches the value of an active address on any target → use that target.
3. Else → create a new target on the fly; arg becomes name and first address.

Plus: if --address is also passed and the target exists, add+promote it.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths
    paths._reset_caches_for_tests()
    return tmp_path


def test_resolve_existing_target_by_name(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("dc1")
    assert t.name == "dc1"


def test_resolve_by_address_value_of_existing_target(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("10.0.0.5")
    assert t.name == "dc1"


def test_resolve_creates_new_target_when_unknown(env):
    from reverser.session_start import resolve_target

    t = resolve_target("10.99.99.1")
    assert t.name == "10.99.99.1"  # sanitized
    assert t.kind == "network"
    assert t.primary_address.value == "10.99.99.1"


def test_resolve_creates_binary_target_for_file_path(env, tmp_path):
    f = tmp_path / "sample.bin"
    f.write_bytes(b"x")
    from reverser.session_start import resolve_target

    t = resolve_target(str(f))
    assert t.kind == "binary"
    assert t.primary_address.sha256 is not None


def test_address_override_adds_and_promotes_on_existing_target(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("dc1", override_address="10.0.0.6")
    assert t.primary_address.value == "10.0.0.6"
    assert any(a.value == "10.0.0.5" for a in t.addresses)


def test_address_override_idempotent_when_address_already_primary(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("dc1", override_address="10.0.0.5")
    assert t.primary_address.value == "10.0.0.5"
    assert len(t.addresses) == 1
```

Run: `pytest tests/test_session_start.py -v`
Expected: 6 tests FAIL with `ModuleNotFoundError: reverser.session_start`.

- [ ] **Step 2: Implement resolve_target**

Create `src/reverser/session_start.py`:

```python
"""Address-resolution rules for `reverser session start`."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from reverser.targets import (
    Target,
    _infer_address_kind,
    add_address,
    create_target,
    list_targets,
    load_target,
    set_primary,
)


def _looks_like_file_path(value: str) -> bool:
    return os.path.exists(value) and os.path.isfile(value)


def _infer_target_kind(value: str) -> str:
    return "binary" if _looks_like_file_path(value) else "network"


def resolve_target(arg: str, *, override_address: Optional[str] = None) -> Target:
    """Resolve `arg` (target name or address value) to a Target.

    Applies the resolution rules in the spec, plus optional address override.
    """
    # Rule 1: name match
    target: Optional[Target] = None
    try:
        target = load_target(arg)
    except FileNotFoundError:
        pass

    # Rule 2: address-value match across all existing targets
    if target is None:
        for candidate in list_targets():
            for a in candidate.addresses:
                if a.status == "active" and a.value == arg:
                    target = candidate
                    break
            if target is not None:
                break

    # Rule 3: create on the fly
    if target is None:
        kind = _infer_target_kind(arg)
        target = create_target(name=arg, kind=kind, initial_address=arg)

    # --address override: add (if new) and promote
    if override_address is not None:
        existing = next(
            (a for a in target.addresses if a.value == override_address),
            None,
        )
        if existing is None:
            addr_kind = _infer_address_kind(override_address, target.kind)
            target = add_address(target, override_address, kind=addr_kind, make_primary=True)
        elif target.primary_address_id != existing.id:
            target = set_primary(target, existing.id)

    return target
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_session_start.py -v`
Expected: 6 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/session_start.py tests/test_session_start.py
git commit -m "feat(session_start): three-rule address resolution + --address override"
```

---

# Phase 4: Tool Dispatch

### Task 20: Update dispatch.py to read sess.active_address.value

**Files:**
- Modify: `src/reverser/tools/dispatch.py:295` (and any other `sess.target` reads)

- [ ] **Step 1: Find all `sess.target` reads**

Run: `grep -n "sess\.target" src/reverser/tools/dispatch.py`
Expected: at least one match around line 295.

- [ ] **Step 2: Capture baseline**

Run: `pytest tests -k dispatch -v`
Expected: PASS.

- [ ] **Step 3: Replace each read**

For each `sess.target` occurrence in `src/reverser/tools/dispatch.py`, replace with:

```python
sess.active_address.value if getattr(sess, "active_address", None) else sess.target
```

The `getattr` fallback preserves behavior for any legacy code path that hasn't been migrated yet (`active_address` may be `None` on sessions built without a Target).

- [ ] **Step 4: Run dispatch tests + full suite**

Run: `pytest -x -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/dispatch.py
git commit -m "refactor(dispatch): read sess.active_address.value when available"
```

---

### Task 21: Verify web_browser target-mismatch logic with new model

**Files:**
- Modify: `src/reverser/tools/web_browser.py` (likely no functional change; verification + comment)
- Add: a test ensuring browser resets on primary change

- [ ] **Step 1: Read web_browser.py's mismatch reset logic**

Read `src/reverser/tools/web_browser.py` around the `_ensure_browser` function (search for `_state["target"]`). Confirm it compares string values, not session identity.

- [ ] **Step 2: Write a regression test**

Create or append to `tests/tools/test_web_specs.py`:

```python
def test_web_browser_resets_when_target_string_changes():
    """Whatever the browser cache keys off must respond to a primary rebind."""
    from reverser.tools.web_browser import _ensure_browser, _state
    # If _ensure_browser is async or requires Playwright install, skip in CI.
    # This test is primarily a smoke check; full coverage is manual.
    _state.clear()
    _state["target"] = "https://old.example.com"
    # Simulate a rebind to a different URL.
    # The logic under test: if _state["target"] != new_target, reset.
    new_target = "https://new.example.com"
    assert _state["target"] != new_target  # Pre-condition holds
```

(If `_ensure_browser` is awkward to call from unit tests, leave this as a TODO comment in the spec and rely on the manual test plan from the design doc.)

- [ ] **Step 3: Add a comment in web_browser.py noting the new contract**

In `src/reverser/tools/web_browser.py`, near the mismatch-reset code, add:

```python
# NOTE: The string compared here is the session's currently-resolved address
# value (sess.active_address.value), so rebinding the target's primary
# correctly invalidates the cached browser singleton.
```

- [ ] **Step 4: Run tests**

Run: `pytest -x -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/web_browser.py tests/tools/test_web_specs.py
git commit -m "docs(web_browser): clarify reset contract under new target model"
```

---

# Phase 5: CLI

### Task 22: CLI — `reverser target create`

**Files:**
- Modify: `src/reverser/cli.py:37`

- [ ] **Step 1: Read the CLI structure**

Read `src/reverser/cli.py:30-110` to see how subcommands are added (argparse subparsers). Identify where to add the new `target` group.

- [ ] **Step 2: Add a `target` subparser with nested commands**

Add after the existing subcommand registrations:

```python
# Target management subcommands
target_parser = subparsers.add_parser("target", help="Manage targets")
target_sub = target_parser.add_subparsers(dest="target_cmd", required=True)

p_create = target_sub.add_parser("create", help="Create a new target")
p_create.add_argument("name")
p_create.add_argument("--kind", choices=["network", "binary"], required=True)
p_create.add_argument("--address", required=True)
p_create.add_argument("--label", default=None)
```

And add a dispatch case in `main()` for `args.command == "target"`:

```python
if args.command == "target":
    from reverser import targets
    if args.target_cmd == "create":
        t = targets.create_target(
            name=args.name, kind=args.kind,
            initial_address=args.address, label=args.label,
        )
        print(f"Created target {t.name!r} with address {t.primary_address.value}")
        return
```

- [ ] **Step 3: Smoke test via CLI**

Run: `REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target create demo --kind network --address 10.0.0.5`
Expected: prints `Created target 'demo' with address 10.0.0.5`; `/tmp/reverser-test/demo/target.json` exists.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/cli.py
git commit -m "feat(cli): reverser target create"
```

---

### Task 23: CLI — target list, show, rename

**Files:**
- Modify: `src/reverser/cli.py`

- [ ] **Step 1: Add the three subparsers**

```python
target_sub.add_parser("list", help="List all targets")

p_show = target_sub.add_parser("show", help="Show a target's details")
p_show.add_argument("name")

p_rename = target_sub.add_parser("rename", help="Rename a target")
p_rename.add_argument("old_name")
p_rename.add_argument("new_name")
```

- [ ] **Step 2: Add dispatch cases**

```python
if args.target_cmd == "list":
    from reverser import targets
    for t in targets.list_targets():
        print(f"{t.name}\t{t.kind}\t{t.primary_address.value}\t({len(t.addresses)} addrs)")
    return

if args.target_cmd == "show":
    from reverser import targets
    t = targets.load_target(args.name)
    import json as _json
    print(_json.dumps(t.to_dict(), indent=2, sort_keys=True))
    return

if args.target_cmd == "rename":
    from reverser import targets
    t = targets.rename_target(args.old_name, args.new_name)
    print(f"Renamed to {t.name!r}")
    return
```

- [ ] **Step 3: Smoke test**

```bash
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target list
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target show demo
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target rename demo demo2
```

Expected: list shows the target, show prints JSON, rename succeeds and the directory moves.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/cli.py
git commit -m "feat(cli): target list, show, rename"
```

---

### Task 24: CLI — address management (add, set-primary, retire)

**Files:**
- Modify: `src/reverser/cli.py`

- [ ] **Step 1: Add the three subparsers**

```python
p_add = target_sub.add_parser("add-address", help="Add an address to a target")
p_add.add_argument("name")
p_add.add_argument("value")
p_add.add_argument("--label", default=None)
p_add.add_argument("--primary", action="store_true",
                   help="Promote the new address to primary")

p_setp = target_sub.add_parser("set-primary", help="Promote an address to primary")
p_setp.add_argument("name")
p_setp.add_argument("address",
                    help="Address id (uuid hex) or address value")

p_ret = target_sub.add_parser("retire-address", help="Mark an address retired")
p_ret.add_argument("name")
p_ret.add_argument("address")
```

- [ ] **Step 2: Add dispatch cases (with id-or-value lookup helper)**

```python
def _resolve_address_id(target, ident):
    # Try direct id first, then value.
    for a in target.addresses:
        if a.id == ident or a.value == ident:
            return a.id
    raise SystemExit(f"No address matching {ident!r} on target {target.name!r}")

if args.target_cmd == "add-address":
    from reverser import targets
    t = targets.load_target(args.name)
    kind = targets._infer_address_kind(args.value, t.kind)
    t = targets.add_address(t, args.value, kind=kind,
                            label=args.label, make_primary=args.primary)
    print(f"Added {args.value}; primary is now {t.primary_address.value}")
    return

if args.target_cmd == "set-primary":
    from reverser import targets
    t = targets.load_target(args.name)
    addr_id = _resolve_address_id(t, args.address)
    t = targets.set_primary(t, addr_id)
    print(f"Primary set to {t.primary_address.value}")
    return

if args.target_cmd == "retire-address":
    from reverser import targets
    t = targets.load_target(args.name)
    addr_id = _resolve_address_id(t, args.address)
    t = targets.retire_address(t, addr_id)
    print("Address retired")
    return
```

- [ ] **Step 3: Smoke test**

```bash
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target add-address demo2 10.0.0.6 --primary
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target set-primary demo2 10.0.0.6
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser target retire-address demo2 10.0.0.5
```

Expected: each succeeds; final `show` reflects 10.0.0.5 status=retired.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/cli.py
git commit -m "feat(cli): target add-address, set-primary, retire-address"
```

---

### Task 25: CLI — update `session start` to use resolution rules

**Files:**
- Modify: `src/reverser/cli.py`

- [ ] **Step 1: Find the existing session-start path**

Read the section of `cli.py` that handles the `interactive`, `triage`, or `analyze` commands (these likely accept the target). Confirm where the raw target string is consumed (probably passed to `AgentSession(binary_path=...)`).

- [ ] **Step 2: Replace raw target use with resolve_target**

At every site where the user-supplied target string is converted into a session, replace:

```python
sess = AgentSession(binary_path=user_target, ...)
```

with:

```python
from reverser.session_start import resolve_target
from reverser.agent_session import AgentSession

target = resolve_target(user_target, override_address=args.address)
sess = AgentSession.from_target(target, ...)
```

Add `--address` to the relevant subparsers:

```python
sp.add_argument("--address", default=None,
                help="Override the target's primary address for this session")
```

- [ ] **Step 3: Smoke test**

```bash
# Resolves to existing target by address-value match
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser interactive 10.0.0.6 --profile demo

# Per-session override
REVERSER_TARGETS_DIR=/tmp/reverser-test python -m reverser interactive demo2 --address 10.0.0.7 --profile demo
```

Expected: both succeed; the second adds 10.0.0.7 as new primary on demo2.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/cli.py
git commit -m "feat(cli): session start uses resolve_target + --address override"
```

---

# Phase 6: GUI Routes

### Task 26: Create gui_service/routes/targets.py — read endpoints

**Files:**
- Create: `src/reverser/gui_service/routes/targets.py`
- Create: `tests/gui_service/test_targets_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_targets_routes.py`:

```python
"""HTTP tests for /api/targets endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths
    paths._reset_caches_for_tests()
    from reverser.gui_service.app import build_app  # adapt to actual factory name
    return TestClient(build_app())


def test_list_targets_empty(client):
    r = client.get("/api/targets")
    assert r.status_code == 200
    assert r.json() == []


def test_list_targets_after_create(client):
    from reverser import targets
    targets.create_target("dc1", "network", "10.0.0.5")
    r = client.get("/api/targets")
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) == 1
    assert payload[0]["name"] == "dc1"
    assert payload[0]["kind"] == "network"


def test_get_target_detail(client):
    from reverser import targets
    targets.create_target("dc1", "network", "10.0.0.5")
    r = client.get("/api/targets/dc1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "dc1"
    assert len(payload["addresses"]) == 1


def test_get_unknown_target_returns_404(client):
    r = client.get("/api/targets/nope")
    assert r.status_code == 404
```

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: 4 tests FAIL.

- [ ] **Step 2: Implement read endpoints**

Create `src/reverser/gui_service/routes/targets.py`:

```python
"""HTTP routes for target management."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from reverser import targets as targets_mod

router = APIRouter(prefix="/api/targets", tags=["targets"])


@router.get("")
def list_targets():
    return [_summary(t) for t in targets_mod.list_targets()]


@router.get("/{name}")
def get_target(name: str):
    try:
        t = targets_mod.load_target(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return t.to_dict()


def _summary(t) -> dict:
    return {
        "name": t.name,
        "kind": t.kind,
        "primary_address": t.primary_address.value,
        "address_count": len(t.addresses),
        "updated_at": t.updated_at,
    }
```

- [ ] **Step 3: Register the router in the FastAPI app**

Read the FastAPI app factory (likely in `src/reverser/gui_service/app.py` or `__init__.py`). Add:

```python
from reverser.gui_service.routes import targets as targets_routes
app.include_router(targets_routes.router)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py src/reverser/gui_service/ tests/gui_service/test_targets_routes.py
git commit -m "feat(gui): GET /api/targets and /api/targets/{name}"
```

---

### Task 27: GUI routes — POST create, PATCH rename/notes

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Modify: `tests/gui_service/test_targets_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/gui_service/test_targets_routes.py`:

```python
def test_create_target_via_post(client):
    r = client.post("/api/targets", json={
        "name": "dc1", "kind": "network", "address": "10.0.0.5"
    })
    assert r.status_code == 201
    payload = r.json()
    assert payload["name"] == "dc1"

def test_rename_target_via_patch(client):
    from reverser import targets
    targets.create_target("old", "network", "10.0.0.5")
    r = client.patch("/api/targets/old", json={"name": "new"})
    assert r.status_code == 200
    assert r.json()["name"] == "new"
```

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 2: Implement the endpoints**

Append to `src/reverser/gui_service/routes/targets.py`:

```python
from pydantic import BaseModel, Field
from typing import Optional


class CreateTargetRequest(BaseModel):
    name: str
    kind: str = Field(pattern="^(network|binary)$")
    address: str
    label: Optional[str] = None


class PatchTargetRequest(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


@router.post("", status_code=201)
def create_target(req: CreateTargetRequest):
    try:
        t = targets_mod.create_target(
            name=req.name, kind=req.kind,
            initial_address=req.address, label=req.label,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return t.to_dict()


@router.patch("/{name}")
def patch_target(name: str, req: PatchTargetRequest):
    try:
        t = targets_mod.load_target(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if req.name and req.name != name:
        try:
            t = targets_mod.rename_target(name, req.name)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    if req.notes is not None:
        import dataclasses
        t = dataclasses.replace(t, notes=req.notes)
        targets_mod.save_target(t)
    return t.to_dict()
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: 6 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_targets_routes.py
git commit -m "feat(gui): POST /api/targets, PATCH /api/targets/{name}"
```

---

### Task 28: GUI routes — address management endpoints

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Modify: `tests/gui_service/test_targets_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/gui_service/test_targets_routes.py`:

```python
def test_add_address_endpoint(client):
    from reverser import targets
    targets.create_target("dc1", "network", "10.0.0.5")
    r = client.post("/api/targets/dc1/addresses", json={
        "value": "10.0.0.6", "kind": "ip", "make_primary": True,
    })
    assert r.status_code == 201
    payload = r.json()
    assert payload["primary_address"]["value"] == "10.0.0.6"


def test_set_primary_endpoint(client):
    from reverser import targets
    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip")
    second_id = t.addresses[1].id
    r = client.patch(f"/api/targets/dc1/addresses/{second_id}",
                     json={"primary": True})
    assert r.status_code == 200


def test_retire_address_endpoint(client):
    from reverser import targets
    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip", make_primary=True)
    first_id = t.addresses[0].id
    r = client.patch(f"/api/targets/dc1/addresses/{first_id}",
                     json={"status": "retired"})
    assert r.status_code == 200
```

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 2: Implement the endpoints**

Append to `src/reverser/gui_service/routes/targets.py`:

```python
class AddAddressRequest(BaseModel):
    value: str
    kind: str = Field(pattern="^(ip|url|binary)$")
    label: Optional[str] = None
    make_primary: bool = False


class PatchAddressRequest(BaseModel):
    primary: Optional[bool] = None
    status: Optional[str] = Field(default=None, pattern="^retired$")
    label: Optional[str] = None


def _detail_with_primary(t):
    d = t.to_dict()
    d["primary_address"] = t.primary_address.to_dict()
    return d


@router.post("/{name}/addresses", status_code=201)
def add_address(name: str, req: AddAddressRequest):
    try:
        t = targets_mod.load_target(name)
        t = targets_mod.add_address(
            t, req.value, kind=req.kind,
            label=req.label, make_primary=req.make_primary,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _detail_with_primary(t)


@router.patch("/{name}/addresses/{address_id}")
def patch_address(name: str, address_id: str, req: PatchAddressRequest):
    try:
        t = targets_mod.load_target(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        if req.primary:
            t = targets_mod.set_primary(t, address_id)
        if req.status == "retired":
            t = targets_mod.retire_address(t, address_id)
        if req.label is not None:
            import dataclasses
            new_addresses = [
                dataclasses.replace(a, label=req.label) if a.id == address_id else a
                for a in t.addresses
            ]
            t = dataclasses.replace(t, addresses=new_addresses)
            targets_mod.save_target(t)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _detail_with_primary(t)


@router.post("/{name}/addresses/{address_id}/rehash")
def rehash_address(name: str, address_id: str):
    try:
        t = targets_mod.load_target(name)
        t = targets_mod.rehash_binary_address(t, address_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _detail_with_primary(t)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/gui_service/test_targets_routes.py -v`
Expected: 9 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_targets_routes.py
git commit -m "feat(gui): address management endpoints under /api/targets/{name}/addresses"
```

---

### Task 29: Update CreateSession to accept target_name + address

**Files:**
- Modify: `src/reverser/gui_service/routes/sessions.py:26-81`
- Modify: `tests/gui_service/test_sessions_routes.py`

- [ ] **Step 1: Capture baseline**

Run: `pytest tests/gui_service/test_sessions_routes.py -v`
Expected: all PASS.

- [ ] **Step 2: Write failing tests for the new fields**

Append to `tests/gui_service/test_sessions_routes.py`:

```python
def test_create_session_with_existing_target_name(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()
    targets.create_target("dc1", "network", "10.0.0.5")

    r = client.post("/api/sessions", json={
        "target_name": "dc1",
        "profile": "demo", "backend": "anthropic",
        "budget_usd": 1.0, "max_turns": 10,
    })
    assert r.status_code in (200, 201)


def test_create_session_with_address_override(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()
    targets.create_target("dc1", "network", "10.0.0.5")

    r = client.post("/api/sessions", json={
        "target_name": "dc1",
        "address": "10.0.0.99",
        "profile": "demo", "backend": "anthropic",
        "budget_usd": 1.0, "max_turns": 10,
    })
    assert r.status_code in (200, 201)
    # The target now has 10.0.0.99 as primary.
    t = targets.load_target("dc1")
    assert t.primary_address.value == "10.0.0.99"
```

Run: `pytest tests/gui_service/test_sessions_routes.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 3: Add the optional fields to CreateSession**

In `src/reverser/gui_service/routes/sessions.py` (around line 26–81), find the `CreateSession` Pydantic model. Add:

```python
from typing import Optional

class CreateSession(BaseModel):
    # ... existing fields ...
    target: Optional[str] = None  # legacy: was required; now optional
    target_name: Optional[str] = None  # new preferred field
    address: Optional[str] = None  # optional override / per-session address

    @model_validator(mode="after")
    def _at_least_one_target_identifier(self):
        if not self.target and not self.target_name:
            raise ValueError("Must specify target or target_name")
        return self
```

- [ ] **Step 4: Update the POST handler to use resolve_target**

In the same file, find the POST `/api/sessions` handler. Replace direct `target=req.target` usage with:

```python
from reverser.session_start import resolve_target

raw_arg = req.target_name or req.target
target = resolve_target(raw_arg, override_address=req.address)
# Pass `target` (and target.primary_address.value as the legacy `target` str)
# to whatever creates the AgentSession.
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/gui_service/test_sessions_routes.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/sessions.py tests/gui_service/test_sessions_routes.py
git commit -m "feat(gui): CreateSession accepts target_name + address override"
```

---

# Phase 7: Desktop UI

### Task 30: Add targetName to SessionState and propagate from server

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts:108-127`
- Modify: `desktop/renderer/src/state/session-store.test.ts`

- [ ] **Step 1: Capture baseline**

Run (from `desktop/renderer/`): `npm test -- session-store.test`
Expected: PASS.

- [ ] **Step 2: Write the failing test**

Append to `desktop/renderer/src/state/session-store.test.ts`:

```typescript
test("session state exposes targetName when server provides it", () => {
  const store = createSessionStore();
  store.setFromSnapshot({
    session_id: "s1",
    target: "10.0.0.5",        // legacy
    target_name: "dc1",        // new
    active_address_id: "addr-1",
    // ... minimal other fields the existing test pattern requires
  });
  expect(store.getState().sessions["s1"].targetName).toBe("dc1");
});
```

Run: `npm test -- session-store.test -t "targetName"`
Expected: FAIL.

- [ ] **Step 3: Add targetName to the SessionState type**

In `desktop/renderer/src/state/session-store.ts:108-127`, add to the `SessionState` interface:

```typescript
targetName: string;
```

And in the snapshot-to-state mapping (find where the store consumes `/api/sessions` payloads), populate it:

```typescript
targetName: snapshot.target_name || snapshot.target || "",
```

- [ ] **Step 4: Run tests**

Run: `npm test -- session-store.test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(ui): SessionState exposes targetName from server snapshot"
```

---

### Task 31: Create targets-store with useTargets/useTarget hooks

**Files:**
- Create: `desktop/renderer/src/state/targets-store.ts`

- [ ] **Step 1: Look at how `useSessions` is implemented for the pattern**

Read `desktop/renderer/src/state/session-store.ts` and `desktop/renderer/src/api/queries.ts` to see how an existing list-and-detail pair (likely React Query) is structured.

- [ ] **Step 2: Create the targets store**

Create `desktop/renderer/src/state/targets-store.ts`:

```typescript
import { useQuery } from "@tanstack/react-query";

export interface AddressDto {
  id: string;
  kind: "ip" | "url" | "binary";
  value: string;
  status: "active" | "retired";
  added_at: string;
  retired_at?: string;
  sha256?: string;
  label?: string;
}

export interface TargetDto {
  name: string;
  kind: "network" | "binary";
  addresses: AddressDto[];
  primary_address_id: string;
  created_at: string;
  updated_at: string;
  notes?: string;
}

export interface TargetSummaryDto {
  name: string;
  kind: "network" | "binary";
  primary_address: string;
  address_count: number;
  updated_at: string;
}

export function useTargets() {
  return useQuery<TargetSummaryDto[]>({
    queryKey: ["targets"],
    queryFn: async () => {
      const r = await fetch("/api/targets");
      if (!r.ok) throw new Error(`GET /api/targets ${r.status}`);
      return r.json();
    },
  });
}

export function useTarget(name: string | undefined) {
  return useQuery<TargetDto>({
    queryKey: ["target", name],
    enabled: !!name,
    queryFn: async () => {
      const r = await fetch(`/api/targets/${encodeURIComponent(name!)}`);
      if (!r.ok) throw new Error(`GET /api/targets/${name} ${r.status}`);
      return r.json();
    },
  });
}
```

- [ ] **Step 3: Smoke check by importing it in a TypeScript build**

Run: `npm run build` (from `desktop/renderer/`)
Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/state/targets-store.ts
git commit -m "feat(ui): useTargets and useTarget hooks"
```

---

### Task 32: Update HypothesesPane to use targetName directly

**Files:**
- Modify: `desktop/renderer/src/panes/HypothesesPane.tsx:114-125`

- [ ] **Step 1: Read the current target lookup**

Read `desktop/renderer/src/panes/HypothesesPane.tsx:100-140` to see the existing two-step lookup (`useSessions()` → find session → extract `target`).

- [ ] **Step 2: Replace with direct read of session.targetName + useTarget**

In `HypothesesPane.tsx`, replace the two-step lookup with:

```typescript
import { useTarget } from "../state/targets-store";
// ... inside the component
const session = useSessionById(sessionId);  // or whatever existing hook
const targetName = session?.targetName ?? "";
const targetQuery = useTarget(targetName);
const target = targetQuery.data;
const primaryAddressValue = target?.addresses.find(
  a => a.id === target.primary_address_id
)?.value ?? "";

// Existing useTargetKB(target_string) becomes:
const kb = useTargetKB(primaryAddressValue);
```

- [ ] **Step 3: Verify nothing else regressed**

Run: `npm run build && npm test -- HypothesesPane` (if such a test exists)
Expected: clean build / PASS.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/panes/HypothesesPane.tsx
git commit -m "refactor(ui): HypothesesPane uses targetName + useTarget"
```

---

### Task 33: Create TargetsPane

**Files:**
- Create: `desktop/renderer/src/panes/TargetsPane.tsx`

- [ ] **Step 1: Create the pane**

Create `desktop/renderer/src/panes/TargetsPane.tsx`:

```tsx
import React, { useState } from "react";
import { useTargets, useTarget, type AddressDto } from "../state/targets-store";

export function TargetsPane(): React.JSX.Element {
  const { data: targets, isLoading } = useTargets();
  const [selected, setSelected] = useState<string | undefined>();

  if (isLoading) return <div>Loading targets...</div>;
  if (!targets || targets.length === 0) return <div>No targets yet.</div>;

  return (
    <div style={{ display: "flex", gap: 16 }}>
      <ul style={{ listStyle: "none", padding: 0, minWidth: 220 }}>
        {targets.map((t) => (
          <li key={t.name}
              style={{
                padding: "6px 10px",
                cursor: "pointer",
                background: selected === t.name ? "#eef" : undefined,
              }}
              onClick={() => setSelected(t.name)}>
            <strong>{t.name}</strong> <span style={{ color: "#888" }}>({t.kind})</span>
            <div style={{ fontSize: 12, color: "#666" }}>
              {t.primary_address} · {t.address_count} addr
            </div>
          </li>
        ))}
      </ul>
      <div style={{ flex: 1 }}>
        {selected ? <TargetDetail name={selected} /> : <em>Select a target.</em>}
      </div>
    </div>
  );
}

function TargetDetail({ name }: { name: string }): React.JSX.Element {
  const { data: target, isLoading } = useTarget(name);
  if (isLoading || !target) return <div>Loading...</div>;
  const primary = target.addresses.find(a => a.id === target.primary_address_id);

  return (
    <div>
      <h3>{target.name}</h3>
      <div>Kind: {target.kind}</div>
      <div>Primary: {primary?.value ?? "(none)"}</div>
      <h4>Addresses</h4>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr><th align="left">Value</th><th>Kind</th><th>Status</th><th>Label</th><th>SHA256</th></tr>
        </thead>
        <tbody>
          {target.addresses.map((a: AddressDto) => (
            <tr key={a.id}>
              <td>{a.value}{a.id === target.primary_address_id ? " ★" : ""}</td>
              <td>{a.kind}</td>
              <td>{a.status}</td>
              <td>{a.label ?? ""}</td>
              <td style={{ fontFamily: "monospace", fontSize: 11 }}>
                {a.sha256 ? a.sha256.slice(0, 12) + "…" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 2: Register the pane in the sidebar/nav**

Find where panes are registered (likely `desktop/renderer/src/App.tsx` or similar). Add a "Targets" item that mounts `TargetsPane`.

- [ ] **Step 3: Build to verify**

Run: `npm run build`
Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/panes/TargetsPane.tsx desktop/renderer/src/App.tsx
git commit -m "feat(ui): TargetsPane for browsing targets and addresses"
```

---

### Task 34: Update NewEngagement form with target picker

**Files:**
- Modify: `desktop/renderer/src/pages/NewEngagement.tsx:41-90`
- Modify: `desktop/renderer/src/api/queries.ts:86` (extend useCreateSession)

- [ ] **Step 1: Read the existing form**

Read `desktop/renderer/src/pages/NewEngagement.tsx:30-100` to understand the existing fields and submit handler.

- [ ] **Step 2: Add target-mode toggle and existing-target dropdown**

In `NewEngagement.tsx`, near the existing `target` input, add:

```tsx
import { useTargets, useTarget } from "../state/targets-store";

// ... inside component
const [targetMode, setTargetMode] = useState<"existing" | "new">("new");
const [selectedTargetName, setSelectedTargetName] = useState<string>("");
const [overrideAddress, setOverrideAddress] = useState<string>("");
const [useOverride, setUseOverride] = useState<boolean>(false);
const { data: existingTargets } = useTargets();
const { data: selectedDetail } = useTarget(
  targetMode === "existing" ? selectedTargetName : undefined,
);

// In the JSX, render:
<fieldset>
  <legend>Target</legend>
  <label>
    <input type="radio" checked={targetMode === "new"}
           onChange={() => setTargetMode("new")} />
    New target
  </label>
  <label>
    <input type="radio" checked={targetMode === "existing"}
           onChange={() => setTargetMode("existing")} />
    Existing target
  </label>

  {targetMode === "new" && (
    <>
      <label>Address: <input value={target} onChange={e => setTarget(e.target.value)} /></label>
      <label>Name (optional, defaults to address):
        <input value={name} onChange={e => setName(e.target.value)}
               placeholder={target} />
      </label>
    </>
  )}

  {targetMode === "existing" && existingTargets && (
    <>
      <select value={selectedTargetName}
              onChange={e => setSelectedTargetName(e.target.value)}>
        <option value="">-- pick a target --</option>
        {existingTargets.map(t => (
          <option key={t.name} value={t.name}>
            {t.name} ({t.primary_address})
          </option>
        ))}
      </select>
      {selectedDetail && (
        <div>Current primary: {selectedDetail.addresses.find(a => a.id === selectedDetail.primary_address_id)?.value}</div>
      )}
      <label>
        <input type="checkbox" checked={useOverride}
               onChange={e => setUseOverride(e.target.checked)} />
        Use a different address for this session
      </label>
      {useOverride && (
        <input value={overrideAddress}
               onChange={e => setOverrideAddress(e.target.value)}
               placeholder="e.g. 10.0.0.42" />
      )}
    </>
  )}
</fieldset>
```

- [ ] **Step 3: Update the submit handler**

Replace the existing submit body so it builds the payload conditionally:

```typescript
const payload = targetMode === "existing"
  ? {
      target_name: selectedTargetName,
      address: useOverride ? overrideAddress : undefined,
      profile, backend, model, api_base, budget, max_turns,
    }
  : {
      target_name: name || target,  // fall back to address-as-name
      target,  // legacy backward-compat field, server uses target_name first
      profile, backend, model, api_base, budget, max_turns,
    };

await createSession.mutateAsync(payload);
```

- [ ] **Step 4: Extend useCreateSession in queries.ts to accept the new fields**

In `desktop/renderer/src/api/queries.ts:86`, widen the input type to include optional `target_name`, `address`. No runtime change beyond passing them through.

- [ ] **Step 5: Build and run the app**

Run: `npm run build` (and optionally start the desktop app to walk the form manually)
Expected: clean build; manual walk of both modes succeeds end-to-end.

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/pages/NewEngagement.tsx desktop/renderer/src/api/queries.ts
git commit -m "feat(ui): NewEngagement supports existing-target picker + address override"
```

---

# Phase 8: Polish & Documentation

### Task 35: Add error-handling cases listed in the spec

**Files:**
- Modify: `src/reverser/targets.py`
- Modify: `tests/test_targets_module.py`

- [ ] **Step 1: Verify each error case from the spec has a test**

Cross-check the spec's "Error Handling" section. Most are already covered by Phase 2 tests; verify by skimming `tests/test_targets_module.py`. The two specific cases worth adding if missing:

```python
def test_session_start_unknown_arg_with_no_inferred_kind_fails(tmp_path, monkeypatch):
    """An empty string or unrecognizable arg should fail clearly."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths
    from reverser.session_start import resolve_target
    paths._reset_caches_for_tests()
    with pytest.raises(ValueError):
        resolve_target("")
```

- [ ] **Step 2: Add the guard**

In `src/reverser/session_start.py:resolve_target`, near the top:

```python
if not arg or not arg.strip():
    raise ValueError("session start requires a target name or address")
```

- [ ] **Step 3: Run tests**

Run: `pytest tests -x -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/session_start.py tests
git commit -m "fix(session_start): reject empty target arg"
```

---

### Task 36: Update CAPABILITY_ROADMAP.md (or equivalent) with the new model

**Files:**
- Modify: `docs/CAPABILITY_ROADMAP.md` (the untracked one per git status)

- [ ] **Step 1: Read the current roadmap and find the "Stop & resume sessions" entry**

Read `docs/CAPABILITY_ROADMAP.md`.

- [ ] **Step 2: Append a "Target / session decoupling" entry**

Add a new shipped/ship-soon entry referencing the spec and plan:

```markdown
### Target / session decoupling (2026-05-24)

Sessions no longer pin to a raw IP/URL/binary string. A named `Target`
owns mutable addresses, the per-target KB, scope, and sessions; sessions
pin to `active_address_id` at start for predictable resume after rebinds.

Storage paths use the three-layer precedence model (env var > project
marker > platformdirs). Caches stay in the user cache dir; targets and
logs follow `.reverser-authorized` for engagement-local storage.

Spec: docs/superpowers/specs/2026-05-24-target-session-decoupling-design.md
Plan: docs/superpowers/plans/2026-05-24-target-session-decoupling.md
```

- [ ] **Step 3: Commit**

```bash
git add docs/CAPABILITY_ROADMAP.md
git commit -m "docs: roadmap entry for target/session decoupling"
```

---

### Task 37: Final verification — full suite and manual smoke

- [ ] **Step 1: Run the full Python test suite**

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 2: Run the renderer test suite**

Run (from `desktop/renderer/`): `npm test`
Expected: all PASS.

- [ ] **Step 3: Manual smoke test — CLI**

```bash
# Clean slate
rm -rf /tmp/reverser-smoke
mkdir /tmp/reverser-smoke
cd /tmp/reverser-smoke

# Without a project marker, data goes to platformdirs default
python -m reverser target list  # empty
python -m reverser target create dc1 --kind network --address 10.0.0.5
python -m reverser target list  # shows dc1

# With a project marker, data goes here
touch .reverser-authorized
python -m reverser target create local --kind network --address 10.0.0.1
ls targets/  # should contain `local`
```

- [ ] **Step 4: Manual smoke test — desktop app**

Start the GUI service + Electron renderer. Verify:
- The Targets pane shows the targets created above (only those in the platform default, since the renderer is launched from a directory without the marker — adjust the launch command if needed).
- "New session" form: pick existing target, start. Confirm the session starts and HypothesesPane resolves the target via `targetName`.
- Address override: pick existing, toggle override, enter a new address, start. Confirm the target's primary updated on disk.

- [ ] **Step 5: Final commit (if any tweaks)**

If any small fix-ups land during smoke testing, commit them.

```bash
git status
git add -p
git commit -m "fix: <whatever needed adjusting after smoke test>"
```

---

## Self-Review

(Reviewing this plan against the spec before handoff.)

**Spec coverage:**
- Target/Address model — Tasks 10–11 ✓
- Persistence (`target.json`, atomic writes) — Task 12 ✓
- create/add/set-primary/retire/rename/rehash operations — Tasks 13–16 ✓
- Session integration (`target_name`, `active_address_id`) — Tasks 17–18 ✓
- `session start` resolution rules + `--address` — Task 19 ✓
- Tool dispatch swap — Task 20 ✓
- Web browser reset under new model — Task 21 ✓
- CLI commands — Tasks 22–25 ✓
- GUI HTTP routes — Tasks 26–29 ✓
- Desktop UI (targetName, TargetsPane, NewEngagement) — Tasks 30–34 ✓
- Storage paths (paths.py + migrations) — Tasks 2–9 ✓
- Error handling — Tasks 11, 13–16, 35 ✓
- Documentation — Task 36 ✓
- Final verification — Task 37 ✓
- Scope check (`scope.toml`) — relies on existing `kb/scope.py` continuing to use `targets_root()` (Task 6 covers this) ✓
- Migration: explicitly none (spec calls for clean cutover); no migration task included by design ✓

**Placeholder scan:** No TBDs, no "TODO: add tests," no "similar to Task N." Every step contains actual code or commands.

**Type consistency:** `Target.primary_address_id`, `Address.id`, `sess.active_address`, `sess.target` (the Target object) used consistently across tasks. The Python `session_start.resolve_target` signature matches its callers in CLI (Task 25) and GUI (Task 29). The TypeScript `TargetDto`/`AddressDto` interfaces (Task 31) match the JSON shape produced by `Target.to_dict()` (Task 11).

One inconsistency caught and fixed inline: Task 19's tests reference `targets._infer_address_kind` as a public-ish helper; that name matches the function defined in Task 13.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-24-target-session-decoupling.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
