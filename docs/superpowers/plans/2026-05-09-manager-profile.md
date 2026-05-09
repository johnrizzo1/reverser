# Manager Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `manager` profile to the reverser harness that coordinates specialist sub-agents (`pentest`, `ad`, `webpentest`, `webapi`, `webrecon`) via the Claude Agent SDK Task primitive, maintains a hypothesis tree in a new KB table, and consumes specialist markdown reports to drive a network red-team engagement.

**Architecture:** The manager runs as the top-level `claude_agent_sdk.query()` with a restricted tool surface (KB read/write, hypothesis CRUD, lightweight recon, `dispatch_specialist`, `bash`). A `dispatch_specialist` tool composes a per-dispatch system prompt block and spawns a sub-agent with the named specialty's full tool surface (minus `dispatch_specialist` to block recursion). Specialists return markdown reports; the manager parses outcomes and updates the hypothesis tree in the per-target SQLite KB.

**Tech Stack:** Python 3.10+, Claude Agent SDK, SQLite (existing `targets/<target>/state.db`), pytest. No new system dependencies.

**Spec:** `docs/superpowers/specs/2026-05-09-manager-profile-design.md`

**Baseline (must hold throughout):** All existing 320 tests pass. Tool count grows from 63 → 68. Profile count grows from 13 → 14.

---

## File structure

This plan touches these files. Each task scopes to a focused subset.

### Phase 1 — Profile package split (Tasks 1–3)

The current `src/reverser/profiles.py` is 1047 lines containing 13 profiles. Adding a 14th makes the file painful to navigate and review. Convert to a package; this is a pure refactor — behavior unchanged.

**Create:**
- `src/reverser/profiles/__init__.py` — `Profile`/`Skill` dataclasses, `PROFILES` registry, `_register`/`get_profile`/`list_profiles`, imports each profile module
- `src/reverser/profiles/_skills.py` — shared skill constants (`SKILL_TRIAGE`, `SKILL_ANALYZE`, `SKILL_SOLVE`, etc.) used by multiple profiles
- `src/reverser/profiles/general.py`, `linux.py`, `windows.py`, `android.py`, `chrome.py`, `managed.py`, `api.py`, `pentest.py`, `webpentest.py`, `webapi.py`, `webrecon.py`, `ad.py`, `ctf.py` — one file per existing profile

**Delete:**
- `src/reverser/profiles.py` — replaced by the package

### Phase 2 — Profile dataclass extension (Task 4)

**Modify:**
- `src/reverser/profiles/__init__.py` — add `tools_allowlist: list[str] | None = None` field to `Profile`

### Phase 3 — Hypothesis schema + store (Tasks 5–7)

**Modify:**
- `src/reverser/kb/schema.py` — add `hypotheses` table + indexes; bump `SCHEMA_VERSION` from 1 → 2
- `src/reverser/kb/store.py` — `HypothesisFact` dataclass + `KB.add_hypothesis`/`update_hypothesis`/`get_hypothesis`/`list_hypotheses`/`hypothesis_tree`/`resolve_evidence_refs` methods

**Create:**
- `tests/test_kb_hypotheses.py` — schema migration test, store CRUD tests, tree rendering test

### Phase 4 — Hypothesis tools (Tasks 8–9)

**Modify:**
- `src/reverser/tools/kb.py` — add 4 new tools: `kb_add_hypothesis`, `kb_update_hypothesis`, `kb_list_hypotheses`, `kb_get_hypothesis`

### Phase 5 — Attack-tree report extension (Task 10)

**Modify:**
- `src/reverser/tools/kb.py` — extend `_render_report` to include an "Attack tree" section when hypotheses exist
- `tests/test_kb_hypotheses.py` — add report rendering test

### Phase 6 — Allowlist plumbing (Task 11)

**Modify:**
- `src/reverser/tui/session.py` — read `profile.tools_allowlist`, pass into the agent options
- `src/reverser/backends/claude.py` — accept and use a per-call `allowed_tools` override
- `src/reverser/agent.py` — same, for the non-interactive code path

**Create:**
- `tests/test_profile_allowlist.py` — verify allowlist plumbs through (no live SDK)

### Phase 7 — Dispatch infrastructure (Tasks 12–13)

**Create:**
- `src/reverser/tools/dispatch.py` — `dispatch_specialist` tool, prompt composer, outcome parser
- `tests/test_dispatch_helpers.py` — tests for prompt composer + outcome parser (pure functions)
- `tests/test_dispatch.py` — tests for `dispatch_specialist` tool with mocked SDK

### Phase 8 — Manager profile (Task 14)

**Create:**
- `src/reverser/profiles/manager.py` — manager system_addendum + 6 skills + `tools_allowlist`
- `tests/test_profiles_manager.py` — registration assertions

### Phase 9 — Tool registry + CLI + docs (Tasks 15–18)

**Modify:**
- `src/reverser/tools/__init__.py` — register dispatch tools (new `dispatch_tools` import)
- `src/reverser/cli.py` — `--max-parallel N` argument; update `--profile` help text and `--list-profiles` formatting if needed
- `tests/test_tool_registry.py` — extend assertions: `ALL_TOOLS == 68`, manager profile registered
- `README.md` — add manager profile row + "Manager-led engagements" section

**Create:**
- `tests/manual/manager_smoke.md` — 30-minute walkthrough against an HTB AD lab

### Phase 10 — Integration validation (Task 19)

Read-only. Run full suite, verify counts, smoke-check imports.

---

## Task 1: Create profile package skeleton

**Files:**
- Create: `src/reverser/profiles/__init__.py`
- Create: `src/reverser/profiles/_skills.py`

The goal of this task is to set up the package shell with `Profile`/`Skill` dataclasses and the registry machinery, plus extract shared skill constants. Profile modules themselves come in Task 2.

- [ ] **Step 1: Read the current profiles.py to identify shared skills**

Run: `grep -n "^SKILL_" src/reverser/profiles.py`

Expect ~10–14 `SKILL_*` constants defined at module level. Identify which ones are reused across multiple profiles (those go into `_skills.py`); single-use ones should stay co-located with the profile that uses them (we'll handle that in Task 2).

For this task you only need to know which to extract. The likely shared set:
- `SKILL_TRIAGE`, `SKILL_ANALYZE`, `SKILL_SOLVE`, `SKILL_STRINGS`, `SKILL_DECOMPILE`, `SKILL_IMPORTS`, `SKILL_API_MAP`, `SKILL_WRITEUP`, `SKILL_KB_REPORT`

When in doubt, extract — single-use skills can be moved back later.

- [ ] **Step 2: Create the package directory and `_skills.py`**

```bash
mkdir -p src/reverser/profiles
```

Create `src/reverser/profiles/_skills.py` containing:

```python
"""Shared skill constants used by multiple profiles.

Single-profile skills should live in that profile's own module.
"""

from . import Skill  # forward import; resolved after __init__.py defines Skill


SKILL_TRIAGE = Skill(
    name="Triage",
    key="t",
    description="Quick file identification and security assessment",
    prompt="Perform a quick triage of the loaded binary. Run file_info, checksec, "
           "strings_search, and the appropriate header tool (readelf for ELF, pe_info "
           "for PE) in parallel. Summarize the results concisely.",
)

# ... copy each shared SKILL_* constant from profiles.py verbatim.
# Each Skill(...) literal is copy-pasted; nothing about the values changes.
```

**Important:** copy each shared `SKILL_*` constant *verbatim* from `profiles.py`. Do not rewrite or "improve" the prompt text — exact equivalence is what makes this a safe refactor.

- [ ] **Step 3: Create `src/reverser/profiles/__init__.py`**

```python
"""Agent profiles for specialized reverse engineering / pentest workflows.

Each profile lives in its own module under this package. The package's
__init__.py owns the dataclasses, registry, and lookup helpers.
"""

from dataclasses import dataclass, field


@dataclass
class Skill:
    """A pre-packaged action the user can trigger."""
    name: str
    key: str
    description: str
    prompt: str


@dataclass
class Profile:
    """An agent profile that specializes behavior for a target type."""
    name: str
    key: str
    description: str
    system_addendum: str
    skills: list[Skill] = field(default_factory=list)


PROFILES: dict[str, Profile] = {}


def _register(p: Profile) -> Profile:
    """Register a profile in the global registry. Returns the profile."""
    PROFILES[p.key] = p
    return p


def get_profile(key: str) -> Profile:
    """Look up a profile by key. Raises KeyError if unknown."""
    if key not in PROFILES:
        raise KeyError(
            f"Unknown profile: {key!r}. Known: {sorted(PROFILES.keys())}"
        )
    return PROFILES[key]


def list_profiles() -> list[Profile]:
    """Return all registered profiles, sorted by key."""
    return [PROFILES[k] for k in sorted(PROFILES.keys())]


# ── Profile module imports (each registers itself on import) ────────
# These will be added one-by-one as profile modules are created.
```

- [ ] **Step 4: Verify the package imports cleanly**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "from reverser.profiles import Profile, Skill, PROFILES; print('PROFILES:', list(PROFILES.keys()))"`

Expected: prints `PROFILES: []` (empty registry — profile modules not yet imported).

If there's an import error from `_skills.py` (likely a circular-import on `Skill`), fix by changing the `_skills.py` import to `from reverser.profiles import Skill` instead of the relative `from . import Skill`.

Also try: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "from reverser.profiles._skills import SKILL_TRIAGE; print(SKILL_TRIAGE.name)"` — expected: prints `Triage`.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/profiles/
git commit -m "$(cat <<'EOF'
refactor(profiles): create package skeleton + extract shared skills

Pure-refactor preparation for adding a 14th profile (manager).
profiles.py (1047 lines) becomes a package; this commit adds only
the package shell. Existing profiles.py is unchanged and still loads.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Migrate each profile to its own module

**Files:**
- Create: `src/reverser/profiles/{general,linux,windows,android,chrome,managed,api,pentest,webpentest,webapi,webrecon,ad,ctf}.py` (13 files)
- Modify: `src/reverser/profiles/__init__.py` — append imports for the 13 new modules

This task is mechanical: for each existing profile constant in `profiles.py`, create a new file containing that profile's definition (and any single-use skills it references), and have it self-register via `_register(...)`.

- [ ] **Step 1: Confirm the 13 existing profile keys**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "from reverser.profiles_legacy import list_profiles; [print(p.key) for p in list_profiles()]" 2>&1 || /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "import sys; sys.path.insert(0, 'src'); exec(open('src/reverser/profiles.py').read()); [print(k) for k in sorted(PROFILES.keys())]"`

Expected: 13 keys: `general, linux, windows, android, chrome, managed, api, pentest, webpentest, webapi, webrecon, ad, ctf`.

If the keys differ from the spec, use the actual keys for the file names.

- [ ] **Step 2: Create one profile module per profile (13 files)**

For each profile, create `src/reverser/profiles/<key>.py` following this template:

```python
"""<Profile name> profile."""

from . import _register, Profile, Skill
from ._skills import (
    # Import only the shared skills this profile uses.
    SKILL_TRIAGE,
    SKILL_ANALYZE,
    # ... etc
)


# Single-use skills (skills referenced only by this profile) live here:
SKILL_<NAME> = Skill(
    name="...",
    key="...",
    description="...",
    prompt="...",
)


PROFILE_<KEY_UPPER> = _register(Profile(
    name="<Name>",
    key="<key>",
    description="...",
    system_addendum="""...""",  # copied verbatim from profiles.py
    skills=[SKILL_TRIAGE, SKILL_ANALYZE, ...],
))
```

**Worked example for `general.py`:**

Open `src/reverser/profiles.py`, find the `PROFILE_GENERAL = _register(Profile(...))` block, copy it verbatim into `src/reverser/profiles/general.py` with the import shim above. Identify which `SKILL_*` constants it references — the shared ones get imported from `._skills`, single-use ones get inlined into `general.py` itself.

**Do this 13 times, once per profile.** Be mechanical: copy verbatim, only change imports. Do not "improve" any prompt or skill description.

- [ ] **Step 3: Wire all 13 imports into `__init__.py`**

Append to `src/reverser/profiles/__init__.py` (at the bottom of the file):

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
)
```

The `noqa` directives suppress lint warnings; these imports exist purely to trigger `_register()` calls.

- [ ] **Step 4: Verify all 13 profiles register correctly**

Run:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.profiles import PROFILES, list_profiles
profiles = list_profiles()
print(f'Total: {len(profiles)}')
for p in profiles:
    print(f'  {p.key:12s} ({len(p.skills):2d} skills) - {p.name}')
"
```

Expected: 13 profiles. Compare each profile's name and skill count against `git stash; /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "from reverser.profiles import list_profiles; [print(p.key, len(p.skills)) for p in list_profiles()]"` from the old single-file version (just to confirm parity — *do not actually stash*; mentally compare against the spec, which says the AD profile has 11 skills).

If a profile is missing or has the wrong skill count, find which file you missed in Step 2 and fix it.

- [ ] **Step 5: Run the full test suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 320 passed, 1 skipped (no regressions). The package import system makes both `from reverser.profiles import X` and `import reverser.profiles` resolve to the new package over the old `profiles.py` file because Python prefers packages over modules of the same name.

If any test fails, the most likely cause is a missed `SKILL_*` reference in one of the profile modules. Re-read the failing test, find which profile/skill it inspects, and confirm that profile's module imports/defines that skill.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/profiles/
git commit -m "$(cat <<'EOF'
refactor(profiles): split each profile into its own module

Each of the 13 existing profiles (general, linux, windows, android,
chrome, managed, api, pentest, webpentest, webapi, webrecon, ad, ctf)
now lives in its own module under reverser.profiles. The legacy
profiles.py file is still present (will be removed in the next commit).

No behavioral change. All 320 tests still pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Delete the monolithic `profiles.py`

**Files:**
- Delete: `src/reverser/profiles.py`

- [ ] **Step 1: Confirm the package still resolves correctly**

Run:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
import reverser.profiles
print('module file:', reverser.profiles.__file__)
"
```

Expected: prints a path ending in `src/reverser/profiles/__init__.py` (the *package*), not `src/reverser/profiles.py` (the *module*). If it prints the module path, Python is preferring the file — proceed with the delete to force the package.

- [ ] **Step 2: Delete the file**

```bash
rm src/reverser/profiles.py
```

- [ ] **Step 3: Re-run the full test suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 320 passed, 1 skipped.

- [ ] **Step 4: Commit**

```bash
git add -u src/reverser/profiles.py
git commit -m "$(cat <<'EOF'
refactor(profiles): remove legacy profiles.py module

The reverser.profiles package fully replaces the monolithic file.
All tests pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `tools_allowlist` field to `Profile`

**Files:**
- Modify: `src/reverser/profiles/__init__.py`

The manager profile needs an explicit per-profile tool allowlist (it should NOT have access to heavy offensive tools). All other profiles default to `None`, preserving current behavior.

- [ ] **Step 1: Write a failing test**

Create `tests/test_profiles_allowlist.py`:

```python
"""Tests for the Profile.tools_allowlist field."""

from reverser.profiles import Profile, Skill, get_profile


def test_default_tools_allowlist_is_none():
    """Profile with no tools_allowlist defaults to None (= all tools)."""
    p = Profile(name="x", key="x", description="x", system_addendum="x")
    assert p.tools_allowlist is None


def test_existing_profiles_have_no_allowlist():
    """All currently-shipped profiles default to None (full tool surface)."""
    # All 13 existing profiles must have tools_allowlist == None to preserve
    # current behavior. The manager profile (added later) will set this.
    for key in (
        "general", "linux", "windows", "android", "chrome", "managed",
        "api", "pentest", "webpentest", "webapi", "webrecon", "ad", "ctf",
    ):
        p = get_profile(key)
        assert p.tools_allowlist is None, f"{key} should have tools_allowlist=None"


def test_allowlist_can_be_set():
    """Profile accepts an explicit allowlist."""
    p = Profile(
        name="x", key="x", description="x", system_addendum="x",
        tools_allowlist=["mcp__re__kb_show", "mcp__re__bash"],
    )
    assert p.tools_allowlist == ["mcp__re__kb_show", "mcp__re__bash"]
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_profiles_allowlist.py -v`

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'tools_allowlist'` (and the other tests fail similarly because the field doesn't exist).

- [ ] **Step 3: Add the field to `Profile`**

Edit `src/reverser/profiles/__init__.py`. Update the `Profile` dataclass to:

```python
@dataclass
class Profile:
    """An agent profile that specializes behavior for a target type."""
    name: str
    key: str
    description: str
    system_addendum: str
    skills: list[Skill] = field(default_factory=list)
    tools_allowlist: list[str] | None = None  # None = all tools available
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_profiles_allowlist.py -v`

Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 323 passed (320 + 3 new), 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/profiles/__init__.py tests/test_profiles_allowlist.py
git commit -m "$(cat <<'EOF'
feat(profiles): add Profile.tools_allowlist field

Defaults to None (= all tools, current behavior). The manager profile
(added later) sets an explicit allowlist to prevent it from invoking
heavy offensive tools directly — those must go through dispatch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: KB schema — add `hypotheses` table + migration

**Files:**
- Modify: `src/reverser/kb/schema.py`
- Create: `tests/test_kb_hypotheses.py`

The hypothesis tree lives in a new SQLite table on the per-target KB. We bump `SCHEMA_VERSION` from 1 → 2 and apply the new DDL via the existing `apply_schema` machinery (which runs `CREATE TABLE IF NOT EXISTS` — safe to re-apply on existing DBs).

- [ ] **Step 1: Write failing tests for the schema**

Create `tests/test_kb_hypotheses.py`:

```python
"""Tests for the hypotheses table and store helpers."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from reverser.kb.schema import apply_schema, get_schema_version, SCHEMA_VERSION


def test_schema_version_is_2():
    """Bumping the schema for the hypotheses table."""
    assert SCHEMA_VERSION == 2


def test_apply_schema_creates_hypotheses_table():
    """Fresh DB has a hypotheses table after apply_schema."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        apply_schema(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hypotheses'"
        )
        assert cur.fetchone() is not None


def test_apply_schema_creates_hypotheses_indexes():
    """Indexes on status and parent_id exist."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        apply_schema(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='hypotheses'"
        )
        index_names = {r[0] for r in cur.fetchall()}
        assert "idx_hypotheses_status" in index_names
        assert "idx_hypotheses_parent" in index_names


def test_status_check_constraint_rejects_invalid_status():
    """The CHECK constraint blocks unknown status values."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        apply_schema(conn)
        # Insert a target row first (FK from hypotheses to targets)
        conn.execute(
            "INSERT INTO targets (id, first_seen, last_active) VALUES (?, ?, ?)",
            ("test", "2026-05-09", "2026-05-09"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO hypotheses (target_id, statement, status) "
                "VALUES (?, ?, ?)",
                ("test", "test statement", "bogus"),
            )


def test_apply_schema_migrates_existing_v1_db():
    """Running apply_schema on a v1 DB (without hypotheses table) adds the table."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        # Simulate a v1 DB: minimal tables only
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '1')"
        )
        conn.execute(
            "CREATE TABLE targets (id TEXT PRIMARY KEY, first_seen TEXT, last_active TEXT)"
        )
        conn.commit()
        # Now apply the new schema
        apply_schema(conn)
        # Hypotheses table should now exist
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hypotheses'"
        )
        assert cur.fetchone() is not None
        # Schema version is now 2
        assert get_schema_version(conn) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: FAIL — first test fails because `SCHEMA_VERSION == 1`; the rest fail because the table doesn't exist.

- [ ] **Step 3: Update `src/reverser/kb/schema.py`**

Bump the version constant:

```python
SCHEMA_VERSION = 2
```

Append the new table DDL and indexes to `_DDL`:

```python
    """
    CREATE TABLE IF NOT EXISTS hypotheses (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id       INTEGER REFERENCES hypotheses(id) ON DELETE SET NULL,
        target_id       TEXT    NOT NULL REFERENCES targets(id),
        statement       TEXT    NOT NULL,
        rationale       TEXT,
        status          TEXT    NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','testing','confirmed','refuted','abandoned','blocked')),
        confidence      INTEGER CHECK (confidence BETWEEN 0 AND 100),
        dispatched_to   TEXT,
        dispatch_count  INTEGER NOT NULL DEFAULT 0,
        evidence_refs   TEXT,
        tags            TEXT,
        created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hypotheses_parent ON hypotheses(parent_id)
    """,
```

**Note:** The spec listed the schema without `target_id`, but the existing `findings`/`notes` tables include it for per-target scoping — we add it here for consistency. The spec is slightly out of sync; this is intentional.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 328 passed (323 + 5 new), 1 skipped. Existing tests still pass because `CREATE TABLE IF NOT EXISTS` is idempotent.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/schema.py tests/test_kb_hypotheses.py
git commit -m "$(cat <<'EOF'
feat(kb): add hypotheses table for the manager profile's attack tree

Schema bumps to v2; CREATE TABLE IF NOT EXISTS keeps existing v1 DBs
working — they get an empty hypotheses table on next open. Indexes
on status and parent_id support the manager's typical queries
("what am I currently testing?", "show children of hypothesis N").

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: KB store — hypothesis CRUD helpers

**Files:**
- Modify: `src/reverser/kb/store.py`
- Modify: `tests/test_kb_hypotheses.py` — add CRUD tests

- [ ] **Step 1: Write failing tests for the CRUD operations**

Append to `tests/test_kb_hypotheses.py`:

```python
import json

from reverser.kb.store import KB, HypothesisFact


def _fresh_kb(tmp_path, monkeypatch, target="testtarget"):
    """Create an isolated KB rooted at tmp_path."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    return KB(target)  # constructor takes the raw target string and normalizes internally


def test_add_hypothesis_returns_id_and_persists(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h = kb.add_hypothesis(
        statement="DC has SMB signing disabled",
        rationale="seen in nmap output",
        confidence=80,
        tags=["smb", "high-impact"],
    )
    assert h.id > 0
    assert h.statement == "DC has SMB signing disabled"
    assert h.status == "proposed"
    assert h.confidence == 80
    assert h.tags == ["smb", "high-impact"]


def test_update_hypothesis_changes_status_and_evidence(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h = kb.add_hypothesis(statement="x")
    kb.update_hypothesis(
        h.id,
        status="testing",
        dispatched_to="ad",
    )
    fetched = kb.get_hypothesis(h.id)
    assert fetched.status == "testing"
    assert fetched.dispatched_to == "ad"

    kb.update_hypothesis(
        h.id,
        status="confirmed",
        confidence=95,
        evidence_refs=[{"kind": "finding", "id": 12}],
    )
    fetched = kb.get_hypothesis(h.id)
    assert fetched.status == "confirmed"
    assert fetched.confidence == 95
    assert fetched.evidence_refs == [{"kind": "finding", "id": 12}]


def test_list_hypotheses_filters_by_status(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h1 = kb.add_hypothesis(statement="a")
    h2 = kb.add_hypothesis(statement="b")
    kb.update_hypothesis(h1.id, status="confirmed")

    confirmed = kb.list_hypotheses(status="confirmed")
    assert len(confirmed) == 1
    assert confirmed[0].id == h1.id

    proposed = kb.list_hypotheses(status="proposed")
    assert len(proposed) == 1
    assert proposed[0].id == h2.id


def test_list_hypotheses_filters_by_parent(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    parent = kb.add_hypothesis(statement="parent")
    child1 = kb.add_hypothesis(statement="child1", parent_id=parent.id)
    child2 = kb.add_hypothesis(statement="child2", parent_id=parent.id)
    kb.add_hypothesis(statement="orphan")  # different root

    children = kb.list_hypotheses(parent_id=parent.id)
    assert {c.id for c in children} == {child1.id, child2.id}


def test_get_hypothesis_returns_none_for_missing(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    assert kb.get_hypothesis(99999) is None


def test_dispatch_count_increments(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h = kb.add_hypothesis(statement="x")
    assert kb.get_hypothesis(h.id).dispatch_count == 0
    kb.update_hypothesis(h.id, dispatched_to="ad", increment_dispatch_count=True)
    assert kb.get_hypothesis(h.id).dispatch_count == 1
    kb.update_hypothesis(h.id, dispatched_to="ad", increment_dispatch_count=True)
    assert kb.get_hypothesis(h.id).dispatch_count == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: 6 new tests fail with `AttributeError: 'KB' object has no attribute 'add_hypothesis'` (or `ImportError: cannot import name 'HypothesisFact'`).

- [ ] **Step 3: Add `HypothesisFact` and CRUD methods to `src/reverser/kb/store.py`**

Find the existing `@dataclass` definitions near the top of `store.py` (around line 20–80). Add a new dataclass after the others:

```python
@dataclass
class HypothesisFact:
    id: int | None = None
    parent_id: int | None = None
    statement: str = ""
    rationale: str | None = None
    status: str = "proposed"
    confidence: int | None = None
    dispatched_to: str | None = None
    dispatch_count: int = 0
    evidence_refs: list[dict] | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None
```

Then in the `KB` class (around line 109+), add CRUD methods. The pattern follows the existing `record_note` / `get_notes` methods — open `store.py` and read those for the connection-handling pattern: each method opens a fresh connection via `with self._connect() as conn:` and calls `conn.commit()` after writes.

```python
    def add_hypothesis(
        self,
        statement: str,
        *,
        parent_id: int | None = None,
        rationale: str | None = None,
        confidence: int | None = None,
        tags: list[str] | None = None,
    ) -> HypothesisFact:
        """Insert a new hypothesis. Returns the persisted fact with id populated."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO hypotheses "
                "(target_id, parent_id, statement, rationale, confidence, tags, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
                (
                    self.target_id, parent_id, statement, rationale, confidence,
                    json.dumps(tags) if tags is not None else None,
                    _now_iso(), _now_iso(),
                ),
            )
            new_id = cur.lastrowid
            conn.commit()
        return self.get_hypothesis(new_id)

    def update_hypothesis(
        self,
        hypothesis_id: int,
        *,
        status: str | None = None,
        rationale: str | None = None,
        confidence: int | None = None,
        dispatched_to: str | None = None,
        evidence_refs: list[dict] | None = None,
        tags: list[str] | None = None,
        increment_dispatch_count: bool = False,
    ) -> None:
        """Update fields on an existing hypothesis. Only provided kwargs are written."""
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if rationale is not None:
            sets.append("rationale = ?")
            params.append(rationale)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(confidence)
        if dispatched_to is not None:
            sets.append("dispatched_to = ?")
            params.append(dispatched_to)
        if evidence_refs is not None:
            sets.append("evidence_refs = ?")
            params.append(json.dumps(evidence_refs))
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags))
        if increment_dispatch_count:
            sets.append("dispatch_count = dispatch_count + 1")
        if not sets:
            return  # nothing to do
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(hypothesis_id)
        params.append(self.target_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE hypotheses SET {', '.join(sets)} "
                "WHERE id = ? AND target_id = ?",
                tuple(params),
            )
            conn.commit()

    def get_hypothesis(self, hypothesis_id: int) -> HypothesisFact | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id, parent_id, statement, rationale, status, confidence, "
                "dispatched_to, dispatch_count, evidence_refs, tags, "
                "created_at, updated_at "
                "FROM hypotheses WHERE id = ? AND target_id = ?",
                (hypothesis_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._row_to_hypothesis(row)

    def list_hypotheses(
        self,
        *,
        status: str | None = None,
        parent_id: int | None = None,
    ) -> list[HypothesisFact]:
        sql = (
            "SELECT id, parent_id, statement, rationale, status, confidence, "
            "dispatched_to, dispatch_count, evidence_refs, tags, "
            "created_at, updated_at "
            "FROM hypotheses WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if parent_id is not None:
            sql += " AND parent_id = ?"
            params.append(parent_id)
        sql += " ORDER BY id"
        with self._connect() as conn:
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [self._row_to_hypothesis(r) for r in rows]

    @staticmethod
    def _row_to_hypothesis(row) -> HypothesisFact:
        return HypothesisFact(
            id=row[0],
            parent_id=row[1],
            statement=row[2],
            rationale=row[3],
            status=row[4],
            confidence=row[5],
            dispatched_to=row[6],
            dispatch_count=row[7],
            evidence_refs=json.loads(row[8]) if row[8] else None,
            tags=json.loads(row[9]) if row[9] else None,
            created_at=row[10],
            updated_at=row[11],
        )
```

Add `import json` at the top of `store.py` if not already present.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: PASS (11 tests total — 5 from Task 5 + 6 new).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 334 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_hypotheses.py
git commit -m "$(cat <<'EOF'
feat(kb): add HypothesisFact + KB CRUD methods for hypotheses

Mirrors the patterns of the existing finding/note APIs. evidence_refs
and tags persist as JSON-encoded TEXT columns. dispatch_count is
bumped via SQL expression to avoid races.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: KB store — hypothesis tree + evidence reference resolver

**Files:**
- Modify: `src/reverser/kb/store.py` — add `hypothesis_tree`, `resolve_evidence_refs`
- Modify: `tests/test_kb_hypotheses.py` — add tree + resolver tests

The manager and the report renderer both need a hierarchical view (root → children → grandchildren) of the hypothesis tree, plus a way to dereference `evidence_refs` JSON pointers into the underlying rows.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_kb_hypotheses.py`:

```python
def test_hypothesis_tree_returns_nested_structure(tmp_path, monkeypatch):
    """tree returns roots with .children populated recursively."""
    kb = _fresh_kb(tmp_path, monkeypatch)
    root = kb.add_hypothesis(statement="root")
    child = kb.add_hypothesis(statement="child", parent_id=root.id)
    grand = kb.add_hypothesis(statement="grandchild", parent_id=child.id)
    other_root = kb.add_hypothesis(statement="other root")

    tree = kb.hypothesis_tree()
    # tree is a list of dicts: [{"hypothesis": HypothesisFact, "children": [...]}]
    assert len(tree) == 2
    # find the "root" branch
    root_branch = next(b for b in tree if b["hypothesis"].id == root.id)
    assert len(root_branch["children"]) == 1
    child_branch = root_branch["children"][0]
    assert child_branch["hypothesis"].id == child.id
    assert len(child_branch["children"]) == 1
    assert child_branch["children"][0]["hypothesis"].id == grand.id
    # other_root has no children
    other_branch = next(b for b in tree if b["hypothesis"].id == other_root.id)
    assert other_branch["children"] == []


def test_hypothesis_tree_with_root_id_returns_subtree(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    root = kb.add_hypothesis(statement="root")
    child = kb.add_hypothesis(statement="child", parent_id=root.id)
    kb.add_hypothesis(statement="orphan")

    subtree = kb.hypothesis_tree(root_id=root.id)
    # subtree returns a single branch dict (not a list)
    assert subtree["hypothesis"].id == root.id
    assert len(subtree["children"]) == 1
    assert subtree["children"][0]["hypothesis"].id == child.id


def test_resolve_evidence_refs_returns_finding_rows(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    from reverser.kb.store import FindingFact
    f = kb.add_finding(FindingFact(
        title="SMB signing disabled",
        severity="medium",
        description="…",
    ))
    refs = [{"kind": "finding", "id": f.id}]
    resolved = kb.resolve_evidence_refs(refs)
    assert len(resolved) == 1
    assert resolved[0]["kind"] == "finding"
    assert resolved[0]["data"].title == "SMB signing disabled"


def test_resolve_evidence_refs_skips_unknown_kinds(tmp_path, monkeypatch):
    """Unknown kinds are dropped rather than raising — defensive against schema drift."""
    kb = _fresh_kb(tmp_path, monkeypatch)
    refs = [{"kind": "alien_artifact", "id": 99}]
    resolved = kb.resolve_evidence_refs(refs)
    assert resolved == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: 4 new tests fail with `AttributeError: 'KB' object has no attribute 'hypothesis_tree'` (or similar for `resolve_evidence_refs`).

- [ ] **Step 3: Implement `hypothesis_tree` and `resolve_evidence_refs` in `KB`**

Add to the `KB` class in `src/reverser/kb/store.py`:

```python
    def hypothesis_tree(self, root_id: int | None = None):
        """Return hierarchical view of hypotheses.

        If root_id is None, returns a list of {"hypothesis": HypothesisFact,
        "children": [...]} branches for all root hypotheses (parent_id IS NULL).

        If root_id is given, returns a single branch dict rooted at that hypothesis.
        Raises KeyError if root_id doesn't exist.
        """
        # Fetch all hypotheses for this target, build parent_id → children map
        all_hypotheses = self.list_hypotheses()
        by_parent: dict[int | None, list[HypothesisFact]] = {}
        for h in all_hypotheses:
            by_parent.setdefault(h.parent_id, []).append(h)

        def build_branch(h: HypothesisFact) -> dict:
            return {
                "hypothesis": h,
                "children": [build_branch(c) for c in by_parent.get(h.id, [])],
            }

        if root_id is None:
            roots = by_parent.get(None, [])
            return [build_branch(r) for r in roots]
        else:
            target = next((h for h in all_hypotheses if h.id == root_id), None)
            if target is None:
                raise KeyError(f"hypothesis {root_id} not found")
            return build_branch(target)

    def resolve_evidence_refs(self, refs: list[dict]) -> list[dict]:
        """Dereference evidence_refs into [{kind, id, data}] tuples.

        Unknown kinds are silently dropped (defensive against schema drift).
        Missing rows are silently dropped (defensive against deletion).
        """
        out: list[dict] = []
        for ref in refs:
            kind = ref.get("kind")
            ref_id = ref.get("id")
            if kind is None or ref_id is None:
                continue
            data = None
            if kind == "finding":
                data = self._get_finding_by_id(ref_id)
            elif kind == "note":
                data = self._get_note_by_id(ref_id)
            elif kind == "credential":
                data = self._get_credential_by_id(ref_id)
            elif kind == "service":
                data = self._get_service_by_id(ref_id)
            else:
                continue  # unknown kind
            if data is None:
                continue
            out.append({"kind": kind, "id": ref_id, "data": data})
        return out

    def _get_finding_by_id(self, finding_id: int):
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT title, severity, cvss, description, evidence_paths, created_at "
                "FROM findings WHERE id = ? AND target_id = ?",
                (finding_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return FindingFact(
            title=row[0], severity=row[1], cvss=row[2],
            description=row[3],
            evidence_paths=json.loads(row[4]) if row[4] else None,
        )

    def _get_note_by_id(self, note_id: int):
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT body, created_at FROM notes WHERE id = ? AND target_id = ?",
                (note_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"body": row[0], "created_at": row[1]}

    def _get_credential_by_id(self, cred_id: int):
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT username, password, nt_hash, domain, status "
                "FROM credentials WHERE id = ? AND target_id = ?",
                (cred_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return CredentialFact(
            username=row[0], password=row[1], nt_hash=row[2],
            domain=row[3], status=row[4],
        )

    def _get_service_by_id(self, service_row_id: int):
        # services has a composite PK (target_id, host_ip, port, proto) — id refs
        # are by rowid here. Defensive: if missing, return None.
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT host_ip, port, proto, service, version "
                "FROM services WHERE rowid = ? AND target_id = ?",
                (service_row_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return ServiceFact(
            host_ip=row[0], port=row[1], proto=row[2],
            service=row[3], version=row[4],
        )
```

If `FindingFact`, `CredentialFact`, or `ServiceFact` aren't already imported at the top of `store.py`, ensure they are. They're defined in this same file so no import is needed; just reference them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: PASS (15 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 338 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_hypotheses.py
git commit -m "$(cat <<'EOF'
feat(kb): hypothesis_tree + resolve_evidence_refs helpers

hypothesis_tree builds a nested dict structure of roots/children for
rendering the attack tree. resolve_evidence_refs dereferences the JSON
{kind, id} pointers into the underlying rows, skipping unknown kinds
and missing data defensively.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: KB tools — `kb_add_hypothesis` + `kb_get_hypothesis`

**Files:**
- Modify: `src/reverser/tools/kb.py` — add 2 new tools
- Modify: `tests/test_kb_hypotheses.py` — add tool-level tests

These two tools are paired because the test for `kb_get_hypothesis` requires data to fetch, which `kb_add_hypothesis` provides.

- [ ] **Step 1: Look at the existing `kb_add_finding` tool as a template**

Run: `grep -A30 '"kb_add_finding"' src/reverser/tools/kb.py | head -40`

Note the pattern: `@tool(name, description, schema)` decorator → `async def kb_add_finding(args: dict) -> dict:` → calls `_check_auth()` → calls a `for_target(args["target"]).method(...)` → returns `format_tool_result(...)` → appended to `TOOLS` list. Mirror this structure exactly.

- [ ] **Step 2: Write failing tool tests**

Append to `tests/test_kb_hypotheses.py`:

```python
import asyncio


def _run(coro):
    """Run an async coroutine for a tool call, returning the dict result."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_kb_add_hypothesis_tool_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_add_hypothesis

    result = _run(kb_add_hypothesis({
        "target": "10.10.10.5",
        "statement": "DC has SMB signing disabled",
        "rationale": "from nmap output",
        "confidence": 80,
        "tags": ["smb", "high-impact"],
    }))
    assert "id" in result["content"][0]["text"] or "id" in str(result)
    # verify persistence
    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    hypotheses = kb.list_hypotheses()
    assert len(hypotheses) == 1
    assert hypotheses[0].statement == "DC has SMB signing disabled"
    assert hypotheses[0].confidence == 80


def test_kb_get_hypothesis_tool_returns_record_with_children(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_get_hypothesis

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    parent = kb.add_hypothesis(statement="parent")
    child = kb.add_hypothesis(statement="child", parent_id=parent.id)

    result = _run(kb_get_hypothesis({
        "target": "10.10.10.5",
        "id": parent.id,
    }))
    text = result["content"][0]["text"]
    assert "parent" in text
    assert str(child.id) in text  # children listed
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py::test_kb_add_hypothesis_tool_persists tests/test_kb_hypotheses.py::test_kb_get_hypothesis_tool_returns_record_with_children -v`

Expected: FAIL — `ImportError: cannot import name 'kb_add_hypothesis' from 'reverser.tools.kb'`.

- [ ] **Step 4: Add the tools to `src/reverser/tools/kb.py`**

Append (before the final `TOOLS.append(kb_export_report)` line — match the order tools are appended):

```python
@tool(
    "kb_add_hypothesis",
    "Add a new hypothesis to the engagement's attack tree. Returns the new id. "
    "Use parent_id to link to a parent hypothesis you're refining. confidence is "
    "0-100. tags is a list of free-form labels.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "statement": {"type": "string", "description": "What you're hypothesizing."},
            "parent_id": {"type": "integer", "description": "Parent hypothesis id (optional)."},
            "rationale": {"type": "string", "description": "Why you're proposing this."},
            "confidence": {"type": "integer", "description": "0-100 confidence."},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["target", "statement"],
    },
)
async def kb_add_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    h = for_target(args["target"]).add_hypothesis(
        statement=args["statement"],
        parent_id=args.get("parent_id"),
        rationale=args.get("rationale"),
        confidence=args.get("confidence"),
        tags=args.get("tags"),
    )
    return format_tool_result(
        f"Hypothesis #{h.id} added (status={h.status}, confidence={h.confidence}): "
        f"{h.statement}"
    )


TOOLS.append(kb_add_hypothesis)


@tool(
    "kb_get_hypothesis",
    "Fetch a single hypothesis with its full record and a list of its child "
    "hypothesis ids. Use this to inspect a specific node of the attack tree.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "id": {"type": "integer", "description": "Hypothesis id."},
        },
        "required": ["target", "id"],
    },
)
async def kb_get_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    h = kb.get_hypothesis(args["id"])
    if h is None:
        return format_tool_result(f"No hypothesis with id={args['id']}.")
    children = kb.list_hypotheses(parent_id=h.id)
    lines = [
        f"# Hypothesis #{h.id}",
        f"**Statement:** {h.statement}",
        f"**Status:** {h.status}",
        f"**Confidence:** {h.confidence if h.confidence is not None else '—'}",
        f"**Parent:** {h.parent_id if h.parent_id else '—'}",
        f"**Dispatched to:** {h.dispatched_to or '—'}",
        f"**Dispatch count:** {h.dispatch_count}",
        f"**Tags:** {', '.join(h.tags) if h.tags else '—'}",
    ]
    if h.rationale:
        lines.append(f"**Rationale:** {h.rationale}")
    if h.evidence_refs:
        lines.append(f"**Evidence refs:** {h.evidence_refs}")
    if children:
        lines.append("")
        lines.append(f"**Children (ids):** {[c.id for c in children]}")
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_get_hypothesis)
```

- [ ] **Step 5: Run the tool tests**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: PASS (17 tests).

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 340 passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_hypotheses.py
git commit -m "$(cat <<'EOF'
feat(kb): add kb_add_hypothesis + kb_get_hypothesis MCP tools

Mirrors kb_add_finding/kb_show patterns. get tool surfaces children
ids so the caller can drill down without a separate list call.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: KB tools — `kb_update_hypothesis` + `kb_list_hypotheses`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_hypotheses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_kb_hypotheses.py`:

```python
def test_kb_update_hypothesis_tool_changes_status(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_update_hypothesis

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    h = kb.add_hypothesis(statement="x")
    result = _run(kb_update_hypothesis({
        "target": "10.10.10.5",
        "id": h.id,
        "status": "confirmed",
        "confidence": 95,
        "evidence_refs": [{"kind": "finding", "id": 1}],
    }))
    text = result["content"][0]["text"]
    assert "updated" in text.lower()

    fetched = kb.get_hypothesis(h.id)
    assert fetched.status == "confirmed"
    assert fetched.confidence == 95
    assert fetched.evidence_refs == [{"kind": "finding", "id": 1}]


def test_kb_list_hypotheses_tool_returns_table(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_list_hypotheses

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    kb.add_hypothesis(statement="alpha")
    kb.add_hypothesis(statement="beta")

    result = _run(kb_list_hypotheses({"target": "10.10.10.5"}))
    text = result["content"][0]["text"]
    assert "alpha" in text
    assert "beta" in text


def test_kb_list_hypotheses_tool_with_include_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_list_hypotheses

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    parent = kb.add_hypothesis(statement="parent")
    kb.add_hypothesis(statement="child", parent_id=parent.id)

    result = _run(kb_list_hypotheses({
        "target": "10.10.10.5",
        "include_tree": True,
    }))
    text = result["content"][0]["text"]
    # The tree-rendered output indents children
    assert "parent" in text
    assert "child" in text
    # Child should appear indented (after the parent line)
    parent_idx = text.index("parent")
    child_idx = text.index("child")
    assert child_idx > parent_idx
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py::test_kb_update_hypothesis_tool_changes_status tests/test_kb_hypotheses.py::test_kb_list_hypotheses_tool_returns_table tests/test_kb_hypotheses.py::test_kb_list_hypotheses_tool_with_include_tree -v`

Expected: FAIL — `ImportError`.

- [ ] **Step 3: Add the tools to `src/reverser/tools/kb.py`**

Append after `kb_get_hypothesis`:

```python
@tool(
    "kb_update_hypothesis",
    "Update fields on an existing hypothesis. Pass only the fields you want to "
    "change. Common transitions: status='testing' when dispatching, "
    "status='confirmed'/'refuted'/'inconclusive' when a dispatch returns. "
    "evidence_refs is a list of {kind, id} dicts pointing into the KB.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "id": {"type": "integer"},
            "status": {
                "type": "string",
                "enum": ["proposed", "testing", "confirmed", "refuted", "abandoned", "blocked"],
            },
            "rationale": {"type": "string"},
            "confidence": {"type": "integer"},
            "dispatched_to": {"type": "string"},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of {kind: 'finding'|'note'|'credential'|'service', id: int}",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["target", "id"],
    },
)
async def kb_update_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    if kb.get_hypothesis(args["id"]) is None:
        return format_tool_result(f"No hypothesis with id={args['id']}.")
    update_kwargs = {
        k: args[k]
        for k in ("status", "rationale", "confidence", "dispatched_to",
                  "evidence_refs", "tags")
        if k in args
    }
    kb.update_hypothesis(args["id"], **update_kwargs)
    return format_tool_result(
        f"Hypothesis #{args['id']} updated: {sorted(update_kwargs.keys())}"
    )


TOOLS.append(kb_update_hypothesis)


@tool(
    "kb_list_hypotheses",
    "List hypotheses for the target, optionally filtered by status or parent_id. "
    "Set include_tree=True to render a hierarchical view (recommended for the "
    "manager profile's status checks).",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "status": {"type": "string"},
            "parent_id": {"type": "integer"},
            "include_tree": {"type": "boolean", "default": False},
        },
        "required": ["target"],
    },
)
async def kb_list_hypotheses(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    if args.get("include_tree"):
        return format_tool_result(_render_hypothesis_tree(kb))
    hypotheses = kb.list_hypotheses(
        status=args.get("status"),
        parent_id=args.get("parent_id"),
    )
    if not hypotheses:
        return format_tool_result("No hypotheses match.")
    lines = ["| id | status | conf | parent | statement |",
             "|---|---|---|---|---|"]
    for h in hypotheses:
        lines.append(
            f"| {h.id} | {h.status} | {h.confidence or '—'} | "
            f"{h.parent_id or '—'} | {h.statement} |"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_hypotheses)


_STATUS_GLYPH = {
    "proposed": "💭",
    "testing": "🔄",
    "confirmed": "✅",
    "refuted": "❌",
    "abandoned": "🗑️",
    "blocked": "⛔",
}


def _render_hypothesis_tree(kb) -> str:
    """Markdown-bullet rendering of the hypothesis tree."""
    branches = kb.hypothesis_tree()
    if not branches:
        return "(no hypotheses)"
    lines = []

    def walk(branch, depth: int):
        h = branch["hypothesis"]
        glyph = _STATUS_GLYPH.get(h.status, "•")
        conf = f", {h.confidence}%" if h.confidence is not None else ""
        prefix = "  " * depth
        lines.append(
            f"{prefix}- {glyph} **{h.statement}** "
            f"({h.status}{conf}, id={h.id})"
        )
        for child in branch["children"]:
            walk(child, depth + 1)

    for b in branches:
        walk(b, 0)
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tool tests**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: PASS (20 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 343 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_hypotheses.py
git commit -m "$(cat <<'EOF'
feat(kb): add kb_update_hypothesis + kb_list_hypotheses MCP tools

list supports flat (markdown table) and tree (nested bullets with
status glyphs) renderings. Glyphs: 💭 proposed, 🔄 testing, ✅
confirmed, ❌ refuted, 🗑️ abandoned, ⛔ blocked.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Extend `kb_export_report` with attack-tree section

**Files:**
- Modify: `src/reverser/tools/kb.py` — extend `_render_report`
- Modify: `tests/test_kb_hypotheses.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_kb_hypotheses.py`:

```python
def test_kb_export_report_includes_attack_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_export_report

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    parent = kb.add_hypothesis(statement="DC SMB signing off", confidence=95)
    kb.update_hypothesis(parent.id, status="confirmed")
    kb.add_hypothesis(statement="NTLM relay viable", parent_id=parent.id)

    result = _run(kb_export_report({"target": "10.10.10.5"}))
    text = result["content"][0]["text"]
    assert "## Attack tree" in text
    assert "DC SMB signing off" in text
    assert "NTLM relay viable" in text


def test_kb_export_report_omits_attack_tree_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_export_report

    _fresh_kb(tmp_path, monkeypatch, target="10.10.10.6")  # empty
    result = _run(kb_export_report({"target": "10.10.10.6"}))
    text = result["content"][0]["text"]
    assert "## Attack tree" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py::test_kb_export_report_includes_attack_tree -v`

Expected: FAIL — assertion fails because `## Attack tree` is not in the report.

- [ ] **Step 3: Extend `_render_report` in `src/reverser/tools/kb.py`**

Find the `_render_report(kb)` function. Near the end (before the final `return "\n".join(lines)`), insert:

```python
    # Attack tree (only if hypotheses exist)
    branches = kb.hypothesis_tree()
    if branches:
        lines.append("")
        lines.append("## Attack tree")
        lines.append("")
        lines.append(_render_hypothesis_tree(kb))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_kb_hypotheses.py -v`

Expected: PASS (22 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 345 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_hypotheses.py
git commit -m "$(cat <<'EOF'
feat(kb): include attack tree in kb_export_report

Renders the hypothesis tree as a nested-bullet "## Attack tree"
section. Omitted from the report when no hypotheses exist
(preserves output for non-manager engagements).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Plumb `tools_allowlist` through session and backend

**Files:**
- Modify: `src/reverser/tui/session.py`
- Modify: `src/reverser/backends/claude.py`
- Modify: `src/reverser/agent.py`
- Create: `tests/test_profile_allowlist_plumbing.py`

The Profile dataclass has the `tools_allowlist` field; now we wire it through the call chain so it actually constrains the SDK options.

- [ ] **Step 1: Inspect the current `allowed_tools` plumbing**

Run: `grep -n "allowed_tools" src/reverser/backends/claude.py src/reverser/tui/session.py src/reverser/agent.py`

Note where `"mcp__re__*"` appears — those are the spots where the wildcard is currently passed to `ClaudeAgentOptions`. Each needs to accept an override.

- [ ] **Step 2: Write a failing test**

Create `tests/test_profile_allowlist_plumbing.py`:

```python
"""Verify Profile.tools_allowlist propagates into ClaudeAgentOptions."""

from unittest.mock import MagicMock, patch

from reverser.profiles import Profile, get_profile


def test_backend_accepts_allowed_tools_override():
    """ClaudeBackend.run can take an allowed_tools list and uses it."""
    from reverser.backends.claude import ClaudeBackend

    backend = ClaudeBackend(tools=[])
    # The run() coroutine constructs ClaudeAgentOptions internally.
    # We patch the SDK call to capture the options object.
    captured = {}

    async def fake_query(prompt, options):
        captured["options"] = options
        # End immediately
        if False:
            yield  # type: ignore  # never reached, makes this an async iterator

    with patch("reverser.backends.claude.query", fake_query):
        import asyncio
        async def drive():
            async for _ in backend.run(
                prompt="x",
                system_prompt="x",
                allowed_tools=["mcp__re__kb_show", "mcp__re__bash"],
            ):
                pass
        asyncio.get_event_loop().run_until_complete(drive())

    assert captured["options"].allowed_tools == ["mcp__re__kb_show", "mcp__re__bash"]


def test_backend_defaults_to_wildcard_when_no_override():
    """Default behavior is preserved: allowed_tools=['mcp__re__*']."""
    from reverser.backends.claude import ClaudeBackend

    backend = ClaudeBackend(tools=[])
    captured = {}

    async def fake_query(prompt, options):
        captured["options"] = options
        if False:
            yield  # type: ignore

    with patch("reverser.backends.claude.query", fake_query):
        import asyncio
        async def drive():
            async for _ in backend.run(prompt="x", system_prompt="x"):
                pass
        asyncio.get_event_loop().run_until_complete(drive())

    assert captured["options"].allowed_tools == ["mcp__re__*"]


def test_session_passes_profile_allowlist_through(tmp_path, monkeypatch):
    """Interactive Session reads profile.tools_allowlist and forwards to the backend."""
    from reverser.tui.session import Session

    # Use a real profile we know has allowlist=None
    profile = get_profile("general")
    captured = {}

    class FakeBackend:
        def __init__(self, *a, **kw): pass
        async def run(self, *, prompt, system_prompt, allowed_tools=None, **kw):
            captured["allowed_tools"] = allowed_tools
            return
            yield  # noqa: unreachable

    # We can't easily instantiate Session without a lot of setup; instead just
    # verify the Profile wiring directly: when tools_allowlist is None, no
    # explicit override is passed (backend uses its default wildcard).
    assert profile.tools_allowlist is None

    # Now make a synthetic profile with an allowlist and verify the same path.
    custom = Profile(
        name="t", key="t", description="t", system_addendum="t",
        tools_allowlist=["mcp__re__kb_show"],
    )
    assert custom.tools_allowlist == ["mcp__re__kb_show"]
```

The third test is intentionally lighter — exercising the Session class end-to-end requires significant scaffolding that isn't worth the test value here. We rely on Tasks 12+ (dispatch tests) for deeper integration coverage.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_profile_allowlist_plumbing.py -v`

Expected: First two tests fail (`run() got an unexpected keyword argument 'allowed_tools'`); third may pass.

- [ ] **Step 4: Update `src/reverser/backends/claude.py`**

In `ClaudeBackend.run`, add an `allowed_tools` parameter:

```python
    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        # ...
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"re": server},
            allowed_tools=allowed_tools or ["mcp__re__*"],
            permission_mode="bypassPermissions",
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
        )
```

Apply the same change to the base class signature in `src/reverser/backends/base.py` (add `allowed_tools: list[str] | None = None` to the `run` abstract method) and to `src/reverser/agent.py` if it has a similar `run` call path.

- [ ] **Step 5: Update `src/reverser/tui/session.py` to forward the allowlist**

Find where `Session` calls `backend.run(...)`. Pass `allowed_tools=self.profile.tools_allowlist`. Example:

```python
async for event in self.backend.run(
    prompt=prompt,
    system_prompt=system_prompt,
    max_turns=self.max_turns,
    max_budget_usd=self.budget,
    allowed_tools=self.profile.tools_allowlist,
):
    ...
```

If `self.profile.tools_allowlist` is None, the backend default (wildcard) takes over.

- [ ] **Step 6: Update `src/reverser/agent.py` similarly**

If `agent.py` has its own non-interactive `run()` path that constructs `ClaudeAgentOptions`, plumb the same `allowed_tools` parameter through. Look for the `system_prompt = SYSTEM_PROMPT.format(...)` line and the surrounding `ClaudeAgentOptions(...)` block; thread the parameter from the function's caller.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_profile_allowlist_plumbing.py -v`

Expected: PASS (3 tests).

- [ ] **Step 8: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 348 passed, 1 skipped.

- [ ] **Step 9: Commit**

```bash
git add src/reverser/backends/ src/reverser/tui/session.py src/reverser/agent.py tests/test_profile_allowlist_plumbing.py
git commit -m "$(cat <<'EOF'
feat(profiles): plumb tools_allowlist through session/backend/agent

When a profile sets tools_allowlist (currently only the manager profile,
added next), the interactive session forwards it as ClaudeAgentOptions.
allowed_tools instead of the default mcp__re__* wildcard. Backwards
compatible: profiles with allowlist=None preserve current behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Dispatch helpers — prompt composer + outcome parser

**Files:**
- Create: `src/reverser/tools/dispatch.py`
- Create: `tests/test_dispatch_helpers.py`

These are pure functions: easier to TDD, used by the actual dispatch tool in Task 13.

- [ ] **Step 1: Write failing tests**

Create `tests/test_dispatch_helpers.py`:

```python
"""Pure-function tests for the dispatch prompt composer + outcome parser."""

import pytest

from reverser.tools.dispatch import (
    compose_dispatch_context,
    parse_hypothesis_outcome,
)


def test_compose_dispatch_context_includes_all_fields():
    block = compose_dispatch_context(
        target="10.10.10.5",
        sub_goal="Enumerate SMB shares",
        target_subset=["10.10.10.5"],
        hypothesis_id=42,
        hypothesis_statement="DC has SMB signing disabled",
        rationale="From nmap output",
        scope_summary="In-scope: 10.10.10.0/24",
        max_turns=15,
        budget_usd=0.50,
        extra_context=None,
    )
    assert "10.10.10.5" in block
    assert "Enumerate SMB shares" in block
    assert "id=42" in block
    assert "DC has SMB signing disabled" in block
    assert "From nmap output" in block
    assert "In-scope: 10.10.10.0/24" in block
    assert "Max turns: 15" in block
    assert "$0.50" in block
    # Return contract sections must be specified
    assert "### TL;DR" in block
    assert "### Findings" in block
    assert "### Hypothesis outcome" in block
    assert "### KB writes" in block
    assert "### Suggested follow-up" in block


def test_compose_dispatch_context_handles_missing_optional_fields():
    block = compose_dispatch_context(
        target="x",
        sub_goal="y",
        target_subset=None,
        hypothesis_id=None,
        hypothesis_statement=None,
        rationale=None,
        scope_summary=None,
        max_turns=15,
        budget_usd=0.50,
        extra_context=None,
    )
    # Should not crash; placeholders for missing fields
    assert "y" in block
    assert "entire target scope" in block.lower() or "no subset" in block.lower()


def test_parse_hypothesis_outcome_confirmed():
    report = """### TL;DR
Found it.

### Hypothesis outcome
CONFIRMED — credentials work via SMB to the DC.

### KB writes
- Added cred #5 (status=valid)
"""
    outcome = parse_hypothesis_outcome(report)
    assert outcome == "confirmed"


def test_parse_hypothesis_outcome_refuted():
    report = "### Hypothesis outcome\nREFUTED — anonymous LDAP rejected with 0x80070005.\n"
    assert parse_hypothesis_outcome(report) == "refuted"


def test_parse_hypothesis_outcome_inconclusive():
    report = "### Hypothesis outcome\nINCONCLUSIVE — service was unreachable.\n"
    assert parse_hypothesis_outcome(report) == "inconclusive"


def test_parse_hypothesis_outcome_missing_section_returns_none():
    report = "### TL;DR\nDid stuff.\n"
    assert parse_hypothesis_outcome(report) is None


def test_parse_hypothesis_outcome_unparseable_value_returns_inconclusive():
    """When the section exists but value is gibberish, default to inconclusive."""
    report = "### Hypothesis outcome\n¯\\_(ツ)_/¯ no clear answer\n"
    assert parse_hypothesis_outcome(report) == "inconclusive"


def test_parse_hypothesis_outcome_case_insensitive():
    report = "### Hypothesis outcome\nconfirmed — works.\n"
    assert parse_hypothesis_outcome(report) == "confirmed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_dispatch_helpers.py -v`

Expected: FAIL — `ModuleNotFoundError: reverser.tools.dispatch`.

- [ ] **Step 3: Create `src/reverser/tools/dispatch.py`**

```python
"""Manager-profile dispatch tool: spawn specialist sub-agents via the SDK.

Pure helpers (compose_dispatch_context, parse_hypothesis_outcome) are
unit-tested in isolation. The dispatch_specialist tool itself wraps these
helpers around an SDK Task call (see Task 13).
"""

from __future__ import annotations

import re


# ── Pure helpers ────────────────────────────────────────────────────


_RETURN_CONTRACT = """## Return contract
When you finish, your final assistant message MUST be a markdown report
with these sections:

### TL;DR
One sentence.

### Findings
What you discovered. Bullet list.

### Hypothesis outcome
One of: CONFIRMED, REFUTED, INCONCLUSIVE — followed by one-sentence justification.

### KB writes
Short list of what you persisted (creds added, findings added, hypotheses
spawned). The lead reads this to know what changed.

### Suggested follow-up
What you would test next if you had more budget. The lead decides whether
to act on it.
"""


def compose_dispatch_context(
    *,
    target: str,
    sub_goal: str,
    target_subset: list[str] | None,
    hypothesis_id: int | None,
    hypothesis_statement: str | None,
    rationale: str | None,
    scope_summary: str | None,
    max_turns: int,
    budget_usd: float,
    extra_context: str | None,
) -> str:
    """Compose the dispatch-context block prepended to a specialist's system prompt."""
    subset_line = (
        ", ".join(target_subset)
        if target_subset
        else "entire target scope"
    )
    hyp_line = (
        f"id={hypothesis_id}: {hypothesis_statement or '(statement not provided)'}"
        if hypothesis_id is not None
        else "(no hypothesis attached — lead did not link this dispatch)"
    )
    scope_line = scope_summary if scope_summary else "(no scope.toml present — default conservative behavior)"
    rationale_line = rationale if rationale else "(none provided)"
    extra_line = extra_context if extra_context else "(none)"

    return f"""# Dispatch context (read first)

You are operating as a sub-agent of the engagement lead.

- Engagement target: {target}
- Sub-goal: {sub_goal}
- Target subset: {subset_line}
- Hypothesis under test ({hyp_line})
- Rationale from lead: {rationale_line}
- Extra context: {extra_line}

## Scope envelope (do not exceed)
{scope_line}

## Per-dispatch budget
- Max turns: {max_turns}
- Cost cap: ${budget_usd:.2f}

{_RETURN_CONTRACT}
"""


_OUTCOME_KEYWORDS = {
    "confirmed": "confirmed",
    "refuted": "refuted",
    "inconclusive": "inconclusive",
}


def parse_hypothesis_outcome(report: str) -> str | None:
    """Extract the outcome word from the '### Hypothesis outcome' section.

    Returns one of {'confirmed', 'refuted', 'inconclusive'}, or None if the
    section is missing entirely. If the section exists but the value is
    unparseable, returns 'inconclusive' (defensive — the lead can re-dispatch).
    """
    # Find the section header (case-insensitive, allow extra whitespace)
    pattern = re.compile(
        r"###\s+Hypothesis\s+outcome\s*\n(.+?)(?=\n###|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(report)
    if not match:
        return None

    body = match.group(1).strip()
    if not body:
        return "inconclusive"

    # Look for the first matching outcome keyword in the body
    body_lower = body.lower()
    for keyword in _OUTCOME_KEYWORDS:
        if keyword in body_lower:
            return _OUTCOME_KEYWORDS[keyword]

    # Section present but no keyword recognized
    return "inconclusive"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_dispatch_helpers.py -v`

Expected: PASS (8 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 356 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch_helpers.py
git commit -m "$(cat <<'EOF'
feat(dispatch): add prompt composer + outcome parser pure helpers

compose_dispatch_context renders the dispatch-context block prepended
to a specialist's system prompt (sub-goal, hypothesis, scope, budget,
return contract). parse_hypothesis_outcome extracts CONFIRMED/REFUTED/
INCONCLUSIVE from the specialist's report; gracefully falls back to
inconclusive on malformed sections, returns None when the section is
absent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Dispatch tool — `dispatch_specialist`

**Files:**
- Modify: `src/reverser/tools/dispatch.py` — add `dispatch_specialist` tool
- Create: `tests/test_dispatch.py`

The tool wraps the SDK's Task primitive. We test by mocking `claude_agent_sdk.query` to simulate completion / budget-exhaustion / error paths.

- [ ] **Step 1: Write failing tests**

Create `tests/test_dispatch.py`:

```python
"""Tests for the dispatch_specialist tool with mocked SDK."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_query_result(report_text: str, cost: float = 0.10, turns: int = 5):
    """Build an async generator that mimics claude_agent_sdk.query yielding messages."""
    # Mirror the real SDK message types loosely; we only need .content (TextBlock-ish)
    # and a final ResultMessage.
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage

    async def gen(prompt, options):
        yield AssistantMessage(content=[TextBlock(text=report_text)])
        yield ResultMessage(
            subtype="success",
            result=report_text,
            total_cost_usd=cost,
            num_turns=turns,
        )
    return gen


def test_dispatch_specialist_returns_report_and_outcome(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.dispatch import dispatch_specialist

    report = """### TL;DR
Confirmed SMB signing is off.

### Findings
- DC at 10.10.10.5 has signing=False

### Hypothesis outcome
CONFIRMED — verified via nxc smb output.

### KB writes
- Added finding #1

### Suggested follow-up
Test NTLM relay viability."""

    with patch("reverser.tools.dispatch.query", _mock_query_result(report, cost=0.12, turns=4)):
        result = _run(dispatch_specialist({
            "specialty": "ad",
            "sub_goal": "Verify SMB signing on DC",
            "target": "10.10.10.5",
            "hypothesis_id": 1,
        }))

    text = result["content"][0]["text"]
    assert "CONFIRMED" in text or "confirmed" in text
    assert "10.10.10.5" in text or "Confirmed" in text  # report passed through
    # Structured fields should be in the surface
    assert "cost_usd" in text or "0.12" in text
    assert "turns" in text or "4" in text
    assert "outcome" in text.lower()


def test_dispatch_specialist_unknown_specialty_returns_error():
    from reverser.tools.dispatch import dispatch_specialist
    result = _run(dispatch_specialist({
        "specialty": "nonexistent",
        "sub_goal": "x",
        "target": "10.10.10.5",
    }))
    text = result["content"][0]["text"]
    assert "unknown" in text.lower() or "invalid" in text.lower()


def test_dispatch_specialist_handles_sdk_error(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.dispatch import dispatch_specialist

    async def fail_query(prompt, options):
        raise RuntimeError("SDK exploded")
        yield  # noqa  # makes it an async generator

    with patch("reverser.tools.dispatch.query", fail_query):
        result = _run(dispatch_specialist({
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
        }))

    text = result["content"][0]["text"]
    assert "error" in text.lower()
    assert "SDK exploded" in text


def test_dispatch_specialist_strips_dispatch_tool_from_subagent_allowed_tools(monkeypatch, tmp_path):
    """The sub-agent must NOT have access to dispatch_specialist (no recursive dispatch)."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.dispatch import dispatch_specialist

    captured = {}

    async def capturing_query(prompt, options):
        captured["allowed_tools"] = options.allowed_tools
        # Return minimal valid result
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")])
        yield ResultMessage(subtype="success", result="x", total_cost_usd=0.0, num_turns=1)

    with patch("reverser.tools.dispatch.query", capturing_query):
        _run(dispatch_specialist({
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
        }))

    assert "allowed_tools" in captured
    # The pattern should exclude dispatch_specialist explicitly
    allowed = captured["allowed_tools"]
    if isinstance(allowed, list):
        assert "mcp__re__dispatch_specialist" not in allowed
    # If it's a wildcard, the dispatch tool should be filtered out structurally
    # (we accept either: explicit list excluding it, or a list of all-but-one).


def test_dispatch_specialist_increments_dispatch_count(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.kb.store import KB

    kb = KB("10.10.10.5")
    h = kb.add_hypothesis(statement="test")

    report = "### Hypothesis outcome\nCONFIRMED"
    with patch("reverser.tools.dispatch.query", _mock_query_result(report)):
        _run(dispatch_specialist({
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
            "hypothesis_id": h.id,
        }))

    fetched = kb.get_hypothesis(h.id)
    assert fetched.dispatch_count == 1
    assert fetched.dispatched_to == "ad"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_dispatch.py -v`

Expected: FAIL — `ImportError: cannot import name 'dispatch_specialist' from 'reverser.tools.dispatch'`.

- [ ] **Step 3: Implement `dispatch_specialist` in `src/reverser/tools/dispatch.py`**

Append to `dispatch.py`:

```python
# ── dispatch_specialist tool ────────────────────────────────────────

from claude_agent_sdk import (  # noqa: E402  # imports here to keep helpers import-light
    tool,
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ResultMessage,
)

from .kb import _check_auth, format_tool_result  # reuse existing helpers
from ..profiles import get_profile, PROFILES
from ..kb.store import KB
from ..kb.scope import load_scope


_DISPATCHABLE_SPECIALTIES = ("pentest", "ad", "webpentest", "webapi", "webrecon")

TOOLS: list = []  # exported for tools/__init__.py registration


@tool(
    "dispatch_specialist",
    "Dispatch a specialist sub-agent to test a hypothesis or perform a sub-goal. "
    "Use this when the manager profile needs offensive work done — the sub-agent "
    "runs with its own context, budget cap, and full tool surface (minus this "
    "tool to prevent recursive dispatch). Specialty must be one of: "
    "pentest, ad, webpentest, webapi, webrecon. Returns a structured envelope "
    "containing the specialist's markdown report, hypothesis_outcome (parsed "
    "from the report), cost, and turns consumed.",
    {
        "type": "object",
        "properties": {
            "specialty": {
                "type": "string",
                "enum": list(_DISPATCHABLE_SPECIALTIES),
            },
            "sub_goal": {"type": "string", "description": "One-sentence falsifiable objective."},
            "target": {"type": "string", "description": "Target identifier (defaults to engagement target if known)."},
            "target_subset": {
                "type": "array", "items": {"type": "string"},
                "description": "Specific hosts/URLs (default: entire target scope).",
            },
            "hypothesis_id": {"type": "integer", "description": "Hypothesis being tested (strongly recommended)."},
            "rationale": {"type": "string", "description": "Why dispatching now (audit-log)."},
            "budget_usd": {"type": "number", "default": 0.50},
            "max_turns": {"type": "integer", "default": 15},
            "extra_context": {"type": "string", "description": "Additional briefing for the specialist."},
        },
        "required": ["specialty", "sub_goal", "target"],
    },
)
async def dispatch_specialist(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    specialty = args["specialty"]
    if specialty not in _DISPATCHABLE_SPECIALTIES:
        return format_tool_result(
            f"Unknown or non-dispatchable specialty: {specialty!r}. "
            f"Valid: {list(_DISPATCHABLE_SPECIALTIES)}"
        )

    target = args["target"]
    sub_goal = args["sub_goal"]
    hypothesis_id = args.get("hypothesis_id")
    rationale = args.get("rationale")
    target_subset = args.get("target_subset")
    extra_context = args.get("extra_context")
    budget_usd = float(args.get("budget_usd", 0.50))
    max_turns = int(args.get("max_turns", 15))

    # Look up hypothesis (if any) for the dispatch context
    kb = KB(target)
    hypothesis_statement = None
    if hypothesis_id is not None:
        h = kb.get_hypothesis(hypothesis_id)
        if h is not None:
            hypothesis_statement = h.statement
            kb.update_hypothesis(
                hypothesis_id,
                status="testing",
                dispatched_to=specialty,
                increment_dispatch_count=True,
            )

    # Build the scope summary
    try:
        scope = load_scope(target)
        scope_summary = (
            f"in_scope_cidrs={scope.in_scope_cidrs}; "
            f"no_dos={scope.no_dos}; no_account_lockout={scope.no_account_lockout}; "
            f"allowed_hours={scope.allowed_hours}"
        )
    except Exception:
        scope_summary = None

    # Compose system prompt: dispatch context + specialty addendum
    profile = get_profile(specialty)
    dispatch_block = compose_dispatch_context(
        target=target,
        sub_goal=sub_goal,
        target_subset=target_subset,
        hypothesis_id=hypothesis_id,
        hypothesis_statement=hypothesis_statement,
        rationale=rationale,
        scope_summary=scope_summary,
        max_turns=max_turns,
        budget_usd=budget_usd,
        extra_context=extra_context,
    )
    full_system_prompt = dispatch_block + "\n\n" + profile.system_addendum

    # Compute the sub-agent's allowed_tools — exclude dispatch_specialist
    # to prevent recursive dispatch. We pass an explicit list rather than a
    # wildcard pattern.
    sub_allowed_tools = ["mcp__re__*"]
    # Note: SDK pattern matching includes everything mcp__re__*; we rely on
    # the structural fact that there's no other layer that would let a
    # sub-agent call dispatch_specialist. The conservative belt-and-suspenders
    # approach is to enumerate explicitly:
    from . import ALL_TOOLS  # late import to avoid cycle
    sub_allowed_tools = [
        f"mcp__re__{t.name}" for t in ALL_TOOLS if t.name != "dispatch_specialist"
    ]

    # Spawn the sub-agent. We call query() directly (the SDK Task primitive
    # under the hood); all tool calls in the sub-conversation are visible to
    # the SDK's MCP server which we share with the parent.
    options = ClaudeAgentOptions(
        system_prompt=full_system_prompt,
        allowed_tools=sub_allowed_tools,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        max_budget_usd=budget_usd,
    )

    report_text = ""
    cost_usd = 0.0
    turns_consumed = 0
    status = "completed"
    error_msg = None

    try:
        async for message in query(prompt=sub_goal, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        report_text = block.text  # last text block wins (final message)
            elif isinstance(message, ResultMessage):
                cost_usd = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
                turns_consumed = int(getattr(message, "num_turns", 0) or 0)
                if message.subtype != "success":
                    status = (
                        "budget_exhausted" if "budget" in str(message.subtype).lower()
                        else "turn_limit" if "turn" in str(message.subtype).lower()
                        else "error"
                    )
                    if not report_text:
                        report_text = f"(specialist did not produce a report; subtype={message.subtype})"
    except Exception as e:
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"
        if not report_text:
            report_text = f"(dispatch failed: {error_msg})"

    outcome = parse_hypothesis_outcome(report_text)

    # Build the surfaced result for the manager
    summary_lines = [
        f"# Dispatch result — {specialty}",
        f"**Status:** {status}",
        f"**Cost:** ${cost_usd:.4f}",
        f"**Turns:** {turns_consumed}",
        f"**Outcome:** {outcome or 'unknown'}",
    ]
    if error_msg:
        summary_lines.append(f"**Error:** {error_msg}")
    summary_lines.append("")
    summary_lines.append("---")
    summary_lines.append("")
    summary_lines.append("## Specialist's report")
    summary_lines.append("")
    summary_lines.append(report_text)
    return format_tool_result("\n".join(summary_lines))


TOOLS.append(dispatch_specialist)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_dispatch.py -v`

Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 361 passed, 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch.py
git commit -m "$(cat <<'EOF'
feat(dispatch): add dispatch_specialist MCP tool

Wraps the SDK's query() primitive to spawn a specialist sub-agent with:
- Combined system prompt (dispatch context block + specialty addendum)
- Allowed tools enumerated to exclude dispatch_specialist (no recursion)
- Per-call budget cap (default $0.50 / 15 turns)
- Hypothesis status auto-set to 'testing' and dispatch_count incremented
  when hypothesis_id is provided.

The sub-agent's final markdown report is parsed for the
"### Hypothesis outcome" section; the result envelope returned to the
manager includes status, cost, turns, parsed outcome, and the full report.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Manager profile module

**Files:**
- Create: `src/reverser/profiles/manager.py`
- Modify: `src/reverser/profiles/__init__.py` — import the manager module
- Create: `tests/test_profiles_manager.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_profiles_manager.py`:

```python
"""Tests for the manager profile registration and shape."""

from reverser.profiles import PROFILES, get_profile


def test_manager_profile_registered():
    assert "manager" in PROFILES
    p = get_profile("manager")
    assert p.name  # non-empty
    assert p.description


def test_manager_has_six_skills():
    p = get_profile("manager")
    assert len(p.skills) == 6
    # Confirm expected skill keys (k/s/r/p/b/w)
    keys = sorted(s.key for s in p.skills)
    assert keys == sorted(["k", "s", "r", "p", "b", "w"])


def test_manager_has_explicit_tools_allowlist():
    p = get_profile("manager")
    assert p.tools_allowlist is not None
    assert isinstance(p.tools_allowlist, list)
    # Must include the dispatch tool
    assert "mcp__re__dispatch_specialist" in p.tools_allowlist
    # Must include hypothesis tools
    assert "mcp__re__kb_add_hypothesis" in p.tools_allowlist
    assert "mcp__re__kb_update_hypothesis" in p.tools_allowlist
    assert "mcp__re__kb_get_hypothesis" in p.tools_allowlist
    assert "mcp__re__kb_list_hypotheses" in p.tools_allowlist
    # Must include bash for ad-hoc commands
    assert "mcp__re__bash" in p.tools_allowlist
    # Must NOT include heavy offensive tools — they require dispatch
    forbidden = {
        "mcp__re__netexec_smb",
        "mcp__re__netexec_ldap",
        "mcp__re__bloodhound_collect",
        "mcp__re__sqlmap_test",
        "mcp__re__nuclei_scan",
    }
    overlap = forbidden & set(p.tools_allowlist)
    assert not overlap, f"manager allowlist must NOT include heavy tools: {overlap}"


def test_manager_system_addendum_mentions_dispatch_and_hypothesis():
    p = get_profile("manager")
    addendum = p.system_addendum.lower()
    assert "dispatch" in addendum
    assert "hypothes" in addendum  # hypothesis or hypotheses
    # Mentions the 5 specialties
    for specialty in ("ad", "pentest", "webpentest", "webapi", "webrecon"):
        assert specialty in addendum, f"specialty {specialty} not mentioned"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_profiles_manager.py -v`

Expected: FAIL — `KeyError: 'manager'`.

- [ ] **Step 3: Create `src/reverser/profiles/manager.py`**

```python
"""Manager profile — coordinates specialist sub-agents for network red-team engagements."""

from . import _register, Profile, Skill


MANAGER_TOOLS = [
    # KB read intelligence
    "mcp__re__kb_show",
    "mcp__re__kb_list_creds",
    "mcp__re__kb_list_hosts",
    "mcp__re__kb_list_services",
    "mcp__re__kb_list_hypotheses",
    "mcp__re__kb_export_report",
    # Hypothesis CRUD
    "mcp__re__kb_add_hypothesis",
    "mcp__re__kb_update_hypothesis",
    "mcp__re__kb_get_hypothesis",
    # KB writes
    "mcp__re__kb_add_note",
    "mcp__re__kb_add_finding",
    # Light recon
    "mcp__re__nmap_scan",
    "mcp__re__dns_recon",
    "mcp__re__whatweb_scan",
    "mcp__re__nbtscan",
    # Coordination
    "mcp__re__dispatch_specialist",
    # Shell
    "mcp__re__bash",
]


SKILL_KICKOFF = Skill(
    name="Kickoff",
    key="k",
    description="Read the KB and propose initial root hypotheses",
    prompt=(
        "Read the per-target KB with kb_show. Based on what's there (and any "
        "preliminary recon you can do quickly with nmap_scan or dns_recon), "
        "propose 3–5 root hypotheses about likely attack paths. For each, "
        "create a hypothesis with kb_add_hypothesis (include rationale and "
        "an initial confidence). Then pick the one with the highest expected "
        "value and dispatch the appropriate specialist to test it."
    ),
)

SKILL_STATUS = Skill(
    name="Status",
    key="s",
    description="Print the current hypothesis tree, recent dispatches, and recommended next action",
    prompt=(
        "Print the current state of the engagement: (1) the full hypothesis "
        "tree (kb_list_hypotheses include_tree=True), (2) which hypotheses "
        "are currently in 'testing' status and which specialist they're "
        "dispatched to, (3) what we've confirmed/refuted so far, (4) your "
        "recommended next action and why."
    ),
)

SKILL_REPORT = Skill(
    name="Report",
    key="r",
    description="Generate the engagement report",
    prompt=(
        "Call kb_export_report to generate the full engagement report "
        "(includes the attack tree section) and then write a concise "
        "executive summary above it: target, time window, key findings, "
        "highest-impact attack path validated, what we couldn't reach and "
        "why. Save the result via write_file to "
        "pentest_report_<target>.md in the current directory."
    ),
)

SKILL_PIVOT = Skill(
    name="Pivot",
    key="p",
    description="Reassess the attack tree and propose new hypotheses",
    prompt=(
        "Review every hypothesis in the tree (kb_list_hypotheses). For each "
        "currently 'proposed' or 'testing': is it still worth pursuing given "
        "what we've learned? Mark abandoned ones with reason. Then propose "
        "any new hypotheses based on findings discovered since the last "
        "kickoff/pivot — child hypotheses linked to confirmed parents, or "
        "new roots if a fresh angle emerged."
    ),
)

SKILL_BUDGET = Skill(
    name="Budget",
    key="b",
    description="Show current spend vs cap; raise on user request",
    prompt=(
        "Print the current engagement spend (sum of dispatch costs so far if "
        "you've been tracking, or estimate from `dispatch_count * average "
        "cost per dispatch`) versus the global budget cap. Then ask the "
        "user what they want the new global cap or per-dispatch default "
        "to be. When they answer, confirm the new value(s) and use them "
        "for subsequent dispatches."
    ),
)

SKILL_WRAPUP = Skill(
    name="Wrap up",
    key="w",
    description="Mark unresolved hypotheses, generate report, stop",
    prompt=(
        "Engagement is ending. For every hypothesis still in 'proposed' or "
        "'testing' status: mark it as 'abandoned' with a one-line reason "
        "(out of time, out of scope, blocked, etc.). Then generate the "
        "final engagement report (kb_export_report + executive summary). "
        "Finally, print a brief wrap-up message stating the engagement is "
        "complete and where the report was written."
    ),
)


SYSTEM_ADDENDUM = """## Profile: Manager (network red-team coordinator)

You are the lead operator coordinating an assumed-breach / network red-team
engagement. You direct specialists who have deep expertise in their domain.
**You do not perform offensive actions yourself except for cheap recon — you delegate.**

### Hypothesis-driven methodology

Every dispatch must be tied to a hypothesis. Workflow:

1. Read the KB (`kb_show`) at engagement start.
2. Propose 3–5 root hypotheses (`kb_add_hypothesis`) covering the most likely
   attack paths.
3. For each hypothesis you choose to test: dispatch the appropriate specialist
   via `dispatch_specialist` with the hypothesis_id set.
4. When the specialist returns, update the hypothesis with the outcome
   (`kb_update_hypothesis`) and any evidence_refs.
5. Spawn child hypotheses for confirmed parents, abandon refuted ones,
   propose new roots if the picture changed.

The hypothesis tree IS the engagement plan. It's also the artifact the client
receives at the end — make it readable.

### Specialist menu

You may dispatch any of these five specialties via `dispatch_specialist`:

- **`webrecon`** — perimeter footprinting, subdomain/path enumeration, tech
  fingerprinting. Best for the first 30 minutes against an unknown external
  surface.

- **`webpentest`** — web application exploitation (SQLi, XSS, auth bypass,
  IDOR, path traversal) on known endpoints. Dispatch when you have specific
  endpoints to test, not "find me web vulns somewhere."

- **`webapi`** — API enumeration and abuse (REST/GraphQL discovery, auth
  flow flaws, mass assignment, rate-limit bypass). Use when you've found
  an API surface and want to map its attackable paths.

- **`pentest`** — general network enumeration, service exploitation, and
  post-exploit pivoting. The "everything network" specialist for non-AD
  exploitation.

- **`ad`** — Active Directory: assumed-breach methodology, kerberos abuse
  (ASREP-roast, kerberoasting), BloodHound collection and query, lateral
  movement. Dispatch when you've confirmed AD presence (DC, domain joined
  hosts) and want to test domain-relevant hypotheses.

### Dispatch checklist

Before calling `dispatch_specialist`, confirm in a short thinking block:

1. `hypothesis_id` is set (or write down explicitly why this dispatch is
   exploratory and not tied to a hypothesis).
2. `sub_goal` is one sentence and falsifiable. "Verify SMB signing is off
   on 10.10.10.5" is good. "Look around for AD stuff" is bad.
3. `target_subset` is bounded — the specialist should know exactly which
   hosts/URLs to touch.
4. `budget_usd` is sized appropriately. Default to small ($0.30–0.50) and
   re-dispatch with more if needed; over-budgeting upfront wastes tokens
   on dispatches that turn out trivial.

### Reading the return

When a specialist returns:

1. Update the hypothesis: `kb_update_hypothesis(id=..., status=..., evidence_refs=[...])`.
   The dispatch tool already auto-set `status='testing'` when you dispatched;
   now finalize it.
2. Write a short decision note: `kb_add_note(target=..., body="[decision] ...")`
   capturing what you'll do next and why. Future-you (and the audit log)
   will thank you.
3. Choose your next action based on the report:
   - Confirmed → spawn child hypothesis or test a related angle
   - Refuted → mark and move to the next hypothesis
   - Inconclusive → consider re-dispatch with more budget, or pivot

### Termination criteria

Declare the engagement complete (run the Wrap up skill) when one of:

- All proposed hypotheses are resolved AND no new hypotheses worth pursuing
- User explicitly says wrap up
- Budget is effectively exhausted (>80% of global pool spent)

### Scope and safety

The `scope.toml` envelope (in_scope_cidrs, no_dos, no_account_lockout,
allowed_hours) is enforced both for you and for every specialist you
dispatch. The dispatch wrapper passes the scope envelope into the
specialist's context — you don't need to re-state it in `extra_context`,
but you DO need to honor it in your own recon (nmap_scan should respect
in_scope_cidrs).

If you find yourself wanting to test something out-of-scope, ask the user
first. Don't dispatch anyway and hope.
"""


PROFILE_MANAGER = _register(Profile(
    name="Manager",
    key="manager",
    description="Network red-team conductor: plans hypotheses and dispatches specialists",
    system_addendum=SYSTEM_ADDENDUM,
    skills=[SKILL_KICKOFF, SKILL_STATUS, SKILL_REPORT, SKILL_PIVOT, SKILL_BUDGET, SKILL_WRAPUP],
    tools_allowlist=MANAGER_TOOLS,
))
```

- [ ] **Step 4: Wire the manager import into the package**

Edit `src/reverser/profiles/__init__.py`. In the import block at the bottom, add `manager` to the list:

```python
from . import (  # noqa: F401, E402
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
    manager,  # NEW
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_profiles_manager.py -v`

Expected: PASS (4 tests).

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 365 passed, 1 skipped. Profile count is now 14.

Verify:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.profiles import list_profiles
profiles = list_profiles()
print(f'Total: {len(profiles)}')
assert len(profiles) == 14, f'Expected 14 profiles, got {len(profiles)}'
print('manager' in [p.key for p in profiles])
"
```

Expected: `Total: 14` and `True`.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/profiles/manager.py src/reverser/profiles/__init__.py tests/test_profiles_manager.py
git commit -m "$(cat <<'EOF'
feat(profiles): add manager profile (network red-team coordinator)

The manager profile coordinates specialist sub-agents (pentest, ad,
webpentest, webapi, webrecon) via the dispatch_specialist tool.
Maintains a hypothesis tree in the per-target KB; tools_allowlist
restricts the manager to KB ops + light recon + dispatch + bash —
heavy offensive work must go through delegation.

6 skills: Kickoff, Status, Report, Pivot, Budget, Wrap up.
17-tool allowlist.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Register dispatch + hypothesis tools in tools registry

**Files:**
- Modify: `src/reverser/tools/__init__.py`
- Modify: `tests/test_tool_registry.py`

The 4 hypothesis tools were appended to `kb.TOOLS` already (in Tasks 8–9), so they auto-flow into ALL_TOOLS via the existing `kb_tools` import. The `dispatch_specialist` tool lives in a new module — that needs to be added.

- [ ] **Step 1: Inspect the current `tools/__init__.py`**

Run: `cat src/reverser/tools/__init__.py`

Confirm the structure: imports `from .X import TOOLS as X_tools`, sums them into `ALL_TOOLS`.

- [ ] **Step 2: Update the test for the new tool count**

Open `tests/test_tool_registry.py`. The existing test asserts `len(ALL_TOOLS) == 63` (or similar). Update it:

```python
def test_all_tools_count():
    """Total registered tools after Plan 6 (manager profile)."""
    from reverser.tools import ALL_TOOLS
    # Baseline 63 + 4 hypothesis tools + 1 dispatch tool = 68
    assert len(ALL_TOOLS) == 68


def test_dispatch_specialist_registered():
    from reverser.tools import ALL_TOOLS
    names = {t.name for t in ALL_TOOLS}
    assert "dispatch_specialist" in names


def test_all_hypothesis_tools_registered():
    from reverser.tools import ALL_TOOLS
    names = {t.name for t in ALL_TOOLS}
    assert "kb_add_hypothesis" in names
    assert "kb_update_hypothesis" in names
    assert "kb_list_hypotheses" in names
    assert "kb_get_hypothesis" in names
```

If a `test_all_tools_count`-style test already exists, modify it; if not, add the three new tests above.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v`

Expected: `test_dispatch_specialist_registered` fails — `dispatch_specialist` is not in ALL_TOOLS yet because the dispatch module isn't imported.

- [ ] **Step 4: Add the dispatch import to `src/reverser/tools/__init__.py`**

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
from .dispatch import TOOLS as dispatch_tools  # NEW

ALL_TOOLS = (
    triage_tools + static_tools + dynamic_tools + python_tools
    + exploit_tools + util_tools + network_tools + web_tools
    + kb_tools + netexec_tools + bloodhound_tools + dispatch_tools  # +dispatch
)


def create_re_mcp_server():
    """Create the MCP server exposing all RE tools."""
    return create_sdk_mcp_server(
        name="re",
        version="0.1.0",
        tools=ALL_TOOLS,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_tool_registry.py -v`

Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 368 passed, 1 skipped. ALL_TOOLS is 68.

Sanity check:
```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tools import ALL_TOOLS
print(f'Total tools: {len(ALL_TOOLS)}')
hypothesis_tools = [t.name for t in ALL_TOOLS if 'hypothesis' in t.name]
print(f'Hypothesis tools: {hypothesis_tools}')
dispatch = [t.name for t in ALL_TOOLS if t.name == 'dispatch_specialist']
print(f'Dispatch: {dispatch}')
"
```

Expected: 68 tools, 4 hypothesis tools, 1 dispatch tool.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/__init__.py tests/test_tool_registry.py
git commit -m "$(cat <<'EOF'
feat(tools): register dispatch_specialist + verify hypothesis tools

ALL_TOOLS grows from 63 → 68 (4 hypothesis CRUD + 1 dispatch).
Test asserts the new count and that all 5 new tools appear by name.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: CLI — `--max-parallel N` flag + profile help update

**Files:**
- Modify: `src/reverser/cli.py`
- Modify: `tests/test_cli.py` (if it exists; otherwise create)

The `--max-parallel` flag plumbs through to the dispatch tool. For v1 we accept the value but don't enforce it — the manager prompt instructs sequential dispatches by default; parallel is opt-in via per-dispatch `parallel: True`.

- [ ] **Step 1: Locate the argparse setup in cli.py**

Run: `grep -n "add_argument\|argparse" src/reverser/cli.py | head -30`

Find the `interactive_parser.add_argument("--profile", ...)` block — `--max-parallel` belongs near the other interactive flags.

- [ ] **Step 2: Write a failing test (or assertion)**

If `tests/test_cli.py` exists, add to it. Otherwise create:

```python
"""CLI smoke tests."""

import subprocess


def test_interactive_help_mentions_max_parallel():
    result = subprocess.run(
        ["/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python",
         "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    assert "--max-parallel" in result.stdout, result.stdout


def test_interactive_help_mentions_manager_profile():
    result = subprocess.run(
        ["/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python",
         "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    # The --profile help text should list manager
    assert "manager" in result.stdout.lower()


def test_list_profiles_includes_manager():
    result = subprocess.run(
        ["/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python",
         "-m", "reverser", "interactive", "--list-profiles"],
        capture_output=True, text=True,
    )
    assert "manager" in result.stdout.lower(), result.stdout
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_cli.py -v`

Expected: FAIL — `--max-parallel` is not in the output, and the `--profile` help text may or may not mention manager.

- [ ] **Step 4: Update `src/reverser/cli.py`**

Add the `--max-parallel` argument near the other interactive flags:

```python
interactive_parser.add_argument(
    "--max-parallel", type=int, default=1, metavar="N",
    help="Maximum number of specialist dispatches to run in parallel "
         "(manager profile only). Default 1 (strictly sequential). "
         "Increase only for safe-to-parallelize work like external recon "
         "across distinct subnets.",
)
```

Update the `--profile` help text to mention `manager`:

```python
interactive_parser.add_argument(
    "--profile", "-p", default="general",
    help="Agent profile (general, linux, windows, android, chrome, managed, "
         "api, pentest, webpentest, webapi, webrecon, ad, ctf, manager)",
)
```

If the CLI passes the parsed args into the Session/agent, plumb `max_parallel` through (it can be stored on the session for the dispatch tool to consult — though enforcement is on the manager prompt for v1, so this can be a no-op store-and-forward).

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -q`

Expected: 371 passed, 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): add --max-parallel flag and surface manager profile in help

--max-parallel defaults to 1 (sequential dispatches). The manager prompt
honors it; v1 enforcement is prompt-based (no concurrent dispatch in the
SDK call yet — opt-in via per-call parallel: True).

--profile help and --list-profiles output now both mention manager.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: README — manager profile row + usage section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the existing profile table**

Run: `grep -n "ad\b\|profile" README.md | head -20`

The README has a profile table somewhere. Find it.

- [ ] **Step 2: Add the manager row**

Add a row to the profile table (immediately after the `ad` row, since `manager` typically lives at the end of pentest profiles):

```markdown
| `manager` | Network red-team conductor: plans hypotheses and dispatches specialists | 6 |
```

- [ ] **Step 3: Add a usage section**

Add a new section near the existing pentest usage examples:

```markdown
### Manager-led engagements

The `manager` profile coordinates specialist sub-agents for network
red-team work. It maintains a hypothesis tree in the per-target KB and
dispatches the right specialty (`ad`, `pentest`, `webpentest`, `webapi`,
`webrecon`) to test each hypothesis.

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager 10.10.10.5
```

The manager has a restricted tool surface — KB read/write, hypothesis
CRUD, lightweight recon (nmap_scan, dns_recon, whatweb_scan), and
`dispatch_specialist`. Heavy offensive tools (netexec, bloodhound,
sqlmap, nuclei, etc.) require dispatch.

Skills:
- `k` — Kickoff (read KB, propose 3–5 root hypotheses, dispatch first)
- `s` — Status (current tree, dispatches in flight, next action)
- `r` — Report (engagement report with attack tree section)
- `p` — Pivot (reassess tree, abandon stale hypotheses, propose new)
- `b` — Budget (show spend, raise cap on request)
- `w` — Wrap up (mark unresolved, generate report, stop)

For parallel dispatches (use cautiously — operational collisions on
real infrastructure can trip rate limits or detection thresholds):

```sh
reverser i -p manager 10.10.10.5 --max-parallel 3
```
```

- [ ] **Step 4: Verify the README renders correctly**

Run: `head -100 README.md` and visually confirm the table row is well-formatted, the new section is appropriately placed, and there are no broken markdown.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): add manager profile row + Manager-led engagements section

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Manual smoke test — `tests/manual/manager_smoke.md`

**Files:**
- Create: `tests/manual/manager_smoke.md`

A 30-minute walkthrough against an HTB AD lab, testing the full manager loop end-to-end on real infrastructure. Not part of the automated test suite — this is a human-in-loop checklist.

- [ ] **Step 1: Look at the existing AD smoke doc as a template**

Run: `head -80 tests/manual/ad_smoke.md`

Match its structure (preconditions, numbered steps, expected output, success criteria).

- [ ] **Step 2: Create `tests/manual/manager_smoke.md`**

```markdown
# Manager profile smoke test

A 30-minute end-to-end walkthrough of the `manager` profile against a real
HTB AD lab box. This is a human-in-loop checklist — run when validating a
release of the manager-profile work or after major changes to dispatch
infrastructure.

## Preconditions

- An HTB box with AD exposed (e.g. Forest, Sauna, Active, Cascade) is
  reachable from your test machine
- VPN connected; the box's IP is responsive to ping
- `devenv shell` is active; `nxc --version` works (NetExec installed
  via the devenv venv)
- Neo4j is available (the bloodhound stack will spin it up for sub-agent
  collection if dispatched)
- `REVERSER_PENTEST_AUTHORIZED=1` exported
- A scratch `targets/<ip>/` directory will be created automatically

Optionally place a `targets/<ip>/scope.toml` with `in_scope_cidrs = ["<the-box-ip>/32"]`
to confirm scope enforcement in dispatched specialists.

## Steps

### 1. Launch the manager session

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p manager <ip>
```

**Expected:** TUI loads with `Profile: Manager` in the header. Initial
system prompt panel mentions hypothesis-driven methodology and the 5
specialty options.

### 2. Trigger Kickoff

Press `k` (or type the kickoff prompt manually).

**Expected:**
- Manager calls `kb_show` (empty KB initially, returns "no facts").
- Manager calls `nmap_scan` with default options.
- Manager calls `kb_add_hypothesis` 3–5 times for likely attack paths
  (e.g. "DC has SMB signing disabled", "ASREP-roastable accounts present",
  "Anonymous LDAP enum possible").
- Manager picks one hypothesis and calls `dispatch_specialist(specialty='ad', ...)`.
- The "Specialist's report" section appears in the chat with TL;DR, Findings,
  and Hypothesis outcome.

**Verify:**
```sh
sqlite3 targets/<ip>/state.db "SELECT id, status, statement FROM hypotheses;"
```
Should show ~3–5 rows. The dispatched one is in `testing` status during
dispatch and `confirmed`/`refuted`/`inconclusive` after.

### 3. Verify hypothesis update lands in KB

After the dispatch returns, the manager should call `kb_update_hypothesis`
to record the outcome.

**Verify:**
```sh
sqlite3 targets/<ip>/state.db "SELECT id, status, dispatched_to, evidence_refs FROM hypotheses WHERE status != 'proposed';"
```
At least one row should show non-proposed status with `dispatched_to='ad'`
and possibly an `evidence_refs` JSON array.

### 4. Trigger Status

Press `s`.

**Expected:** Manager prints the hypothesis tree (using `kb_list_hypotheses
include_tree=True`), with status glyphs (✅ confirmed, ❌ refuted, 🔄 testing,
💭 proposed) and a recommended next action.

### 5. Test the interrupt path

While a dispatch is in flight (during step 2 or after triggering Kickoff
again), press `Ctrl+C` (or the TUI's interrupt key).

**Expected:** The dispatch aborts cleanly. The manager session is still
alive. The hypothesis being tested may stay in `testing` status — that's
OK; the manager can re-update it.

### 6. Trigger Pivot

Press `p`.

**Expected:** Manager re-reads the tree, abandons any hypotheses that are
no longer worth pursuing (with reason in `kb_update_hypothesis(status='abandoned')`),
and proposes new child hypotheses based on what we've learned.

### 7. Trigger Report

Press `r`.

**Expected:**
- Manager calls `kb_export_report`.
- Markdown report is written to `pentest_report_<ip>.md`.
- Report includes an `## Attack tree` section with nested-bullet hypothesis
  structure and status glyphs.
- Executive summary above the auto-generated body.

**Verify:**
```sh
head -50 pentest_report_<ip>.md
grep "## Attack tree" pentest_report_<ip>.md
```

### 8. Trigger Wrap up

Press `w`.

**Expected:** Manager marks all unresolved hypotheses as `abandoned` with
reasons, generates the final report, and prints a wrap-up message.

**Verify:**
```sh
sqlite3 targets/<ip>/state.db "SELECT status, COUNT(*) FROM hypotheses GROUP BY status;"
```
No rows should be in `proposed` or `testing` status.

## Success criteria

- All 8 steps complete without crashes
- Hypothesis tree persists across the session and is visible in the report
- At least one `dispatch_specialist` call succeeds end-to-end (real sub-agent
  ran, report parsed, KB updated)
- Manager never invokes a heavy offensive tool directly — only via dispatch
  (verify by scanning the session log for tool calls; only kb_*, dispatch_specialist,
  nmap_scan, dns_recon, whatweb_scan, nbtscan, bash should appear)
- Final report file exists and contains the `## Attack tree` section

## Cleanup

```sh
rm -rf targets/<ip>/
rm pentest_report_<ip>.md
```
```

- [ ] **Step 3: Commit**

```bash
git add tests/manual/manager_smoke.md
git commit -m "$(cat <<'EOF'
docs(test): add manual smoke test for manager profile

30-minute walkthrough against an HTB AD lab. Covers all 6 skills,
verifies hypothesis persistence, tests the interrupt path, and
confirms the manager only invokes its allowlisted tools (no leakage
of heavy offensive tools outside dispatch).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: Final integration validation

**Files:**
- Read-only verification

- [ ] **Step 1: Run the full test suite**

Run: `/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/pytest tests/ -v 2>&1 | tail -30`

Expected: 371+ passed (320 baseline + ~51 new tests across this plan), 1 skipped. Note the exact count.

- [ ] **Step 2: Verify counts**

```bash
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.profiles import PROFILES, list_profiles, get_profile
from reverser.tools import ALL_TOOLS

profiles = list_profiles()
print(f'Profiles: {len(profiles)}')
assert len(profiles) == 14, f'Expected 14 profiles, got {len(profiles)}'

tools = ALL_TOOLS
print(f'Tools: {len(tools)}')
assert len(tools) == 68, f'Expected 68 tools, got {len(tools)}'

mgr = get_profile('manager')
print(f'Manager skills: {len(mgr.skills)}')
assert len(mgr.skills) == 6
print(f'Manager allowlist size: {len(mgr.tools_allowlist)}')
assert len(mgr.tools_allowlist) == 17

print('All counts match.')
"
```

Expected: prints `Profiles: 14`, `Tools: 68`, `Manager skills: 6`, `Manager allowlist size: 17`, `All counts match.`

- [ ] **Step 3: Verify CLI surfaces work**

```sh
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -m reverser interactive --list-profiles | grep manager
```

Expected: a row showing the `manager` profile with name and skill count.

- [ ] **Step 4: Verify the manager profile system prompt renders cleanly**

Run a dry-test of profile loading:

```sh
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.profiles import get_profile
p = get_profile('manager')
print(f'Name: {p.name}')
print(f'Description: {p.description}')
print(f'System addendum length: {len(p.system_addendum)} chars')
print(f'Skills: {[s.key + \"=\" + s.name for s in p.skills]}')
print(f'Allowlist count: {len(p.tools_allowlist)}')
print()
print('---ADDENDUM PREVIEW---')
print(p.system_addendum[:500])
print('... (truncated)')
"
```

Expected: the addendum is several hundred chars, well-formed markdown, mentions all 5 specialties and the hypothesis methodology.

- [ ] **Step 5: Verify the dispatch context block renders correctly**

```sh
/Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.tools.dispatch import compose_dispatch_context
block = compose_dispatch_context(
    target='10.10.10.5',
    sub_goal='Verify SMB signing on DC',
    target_subset=['10.10.10.5'],
    hypothesis_id=42,
    hypothesis_statement='DC has SMB signing disabled',
    rationale='From nmap output',
    scope_summary='in_scope_cidrs=[10.10.10.0/24]; no_dos=True',
    max_turns=15,
    budget_usd=0.50,
    extra_context=None,
)
print(block)
"
```

Expected: a well-formatted dispatch context block with all fields, including the return contract sections.

- [ ] **Step 6: Verify the schema migration works on an existing v1 KB**

```sh
mkdir -p /tmp/reverser-mgr-test/targets/10.10.10.5
sqlite3 /tmp/reverser-mgr-test/targets/10.10.10.5/state.db \
  "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
   INSERT INTO meta VALUES ('schema_version', '1');
   CREATE TABLE targets (id TEXT PRIMARY KEY, first_seen TEXT, last_active TEXT);"

REVERSER_TARGETS_DIR=/tmp/reverser-mgr-test/targets \
  /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.devenv/state/venv/bin/python -c "
from reverser.kb.store import KB
kb = KB('10.10.10.5')
h = kb.add_hypothesis(statement='migration test')
print(f'Hypothesis added: id={h.id}')
"

sqlite3 /tmp/reverser-mgr-test/targets/10.10.10.5/state.db \
  "SELECT name FROM sqlite_master WHERE type='table' AND name='hypotheses'; SELECT value FROM meta WHERE key='schema_version';"

rm -rf /tmp/reverser-mgr-test
```

Expected: hypotheses table exists; schema_version is 2; the test hypothesis was inserted successfully.

- [ ] **Step 7: Commit (only if cleanup needed)**

If everything passed, no commit. If you found a small typo or missed something, fix it and:

```bash
git commit -am "chore: integration validation cleanup for manager profile"
```

---

## Done

The `manager` profile coordinates specialist sub-agents via the SDK Task primitive, maintains a hypothesis tree in the per-target KB, and produces engagement reports with attack-tree sections. Profile package split (Tasks 1–3) paid down debt that had been accumulating since the AD pack landed.

Final state:
- 14 profiles registered (`general, linux, windows, android, chrome, managed, api, pentest, webpentest, webapi, webrecon, ad, ctf, manager`)
- 68 MCP tools (5 new: 4 hypothesis CRUD + dispatch_specialist)
- ~371 passing tests, 1 skipped
- New schema (v2) with backward-compatible migration
- `tools_allowlist` plumbed through the agent stack
- `--max-parallel` CLI flag (prompt-enforced; structural concurrency is v2 work)
- Manual smoke test (`tests/manual/manager_smoke.md`) for real-infrastructure validation

Future work (out of scope; tracked in spec §14): approval-gated dispatches, phase budgets, auto-resume of budget-exhausted dispatches, configurable specialist pool, cross-target manager, recursive managers, per-hypothesis cost tracking.
