# Refocus Target IP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-point an engagement at a new IP (e.g. after an HTB reset) — from the GUI, a running session, or an agent tool — while preserving the per-target KB and remapping host/service rows old→new.

**Architecture:** A shared `refocus_target()` core (add/promote address → `KB.remap_address` old→new → optional `/etc/hosts` → scope-checked) with thin surfaces: an `AgentSession.refocus_address()` live-session updater, a `kb_refocus_target` agent tool, and a `POST /api/targets/{target}/refocus` GUI endpoint + a renderer control.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Pydantic, `claude-agent-sdk` `@tool`, pytest/pytest-asyncio; desktop renderer React + vitest.

**Spec:** [docs/superpowers/specs/2026-05-30-refocus-target-ip-design.md](../specs/2026-05-30-refocus-target-ip-design.md)

**Test commands (worktree note):** Python — `PYTHONPATH="$PWD/src" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.venv/bin/python -m pytest <args>`. Frontend — from `desktop/`, symlink `node_modules` from the main checkout, then `node_modules/.bin/vitest run --root renderer <args>`.

---

## File Structure

- **Create** `src/reverser/refocus.py` — `RefocusResult`, `rewrite_hosts_entry()`, `refocus_target()`.
- **Modify** `src/reverser/kb/store.py` — add `KB.remap_address(old_ip, new_ip) -> dict`.
- **Modify** `src/reverser/agent_session.py` — add `AgentSession.refocus_address(new_address)`.
- **Modify** `src/reverser/tools/kb.py` — add `kb_refocus_target` tool.
- **Modify** `src/reverser/gui_service/routes/targets.py` — add `POST /api/targets/{target}/refocus`.
- **Modify** `src/reverser/gui_service/session_manager.py` — helper to refocus the active session if it matches the target.
- **Frontend** `desktop/renderer/src/` — API client method, session-store action, refocus control + tests.
- **Tests** `tests/test_kb_remap.py`, `tests/test_refocus.py`, `tests/test_refocus_etc_hosts.py`, extend `tests/test_session_resume.py`, `tests/test_kb_tools.py`, new `tests/gui_service/test_refocus_route.py`.

**Confirm-before-editing notes (call out in your report if reality differs):**
- The renderer target components: spec referenced `TargetOverview` / `TargetsPanel`; the actual files may be `pages/TargetOverview.tsx` and `layout/TargetsPane.tsx`. Grep `desktop/renderer/src` for the target detail view before editing.
- `AgentSession` per-turn context: confirm `self.target` (the engagement string) is read when each turn's prompt/system context is built, so updating it refocuses subsequent turns.

---

## Task 1: `KB.remap_address` (store)

**Files:**
- Modify: `src/reverser/kb/store.py`
- Test: `tests/test_kb_remap.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_kb_remap.py`:

```python
from reverser.kb.store import KB, HostFact, ServiceFact


def _fresh_kb(tmp_path, monkeypatch, target="recon"):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    return KB(target)


def test_remap_simple_rename(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1", hostname="box.htb"))
    kb.record_service(ServiceFact(host_ip="10.0.0.1", port=80, proto="tcp", service="http"))
    counts = kb.remap_address("10.0.0.1", "10.0.0.2")
    assert counts["hosts"] == 1 and counts["services"] == 1
    hosts = kb.get_hosts()
    assert [h.ip for h in hosts] == ["10.0.0.2"]
    assert hosts[0].hostname == "box.htb"  # fields carried over
    assert [s.host_ip for s in kb.get_services()] == ["10.0.0.2"]


def test_remap_merges_on_host_conflict(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1", hostname="box.htb"))
    kb.record_host(HostFact(ip="10.0.0.2", os="Linux"))   # new ip already present
    kb.remap_address("10.0.0.1", "10.0.0.2")
    hosts = kb.get_hosts()
    assert [h.ip for h in hosts] == ["10.0.0.2"]           # old row gone, no dup
    # merged: new row keeps its os, gains hostname from the old row
    assert hosts[0].os == "Linux" and hosts[0].hostname == "box.htb"


def test_remap_skips_duplicate_service(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_service(ServiceFact(host_ip="10.0.0.1", port=80, proto="tcp", service="http"))
    kb.record_service(ServiceFact(host_ip="10.0.0.2", port=80, proto="tcp", service="http"))
    kb.remap_address("10.0.0.1", "10.0.0.2")
    svcs = kb.get_services()
    assert len(svcs) == 1 and svcs[0].host_ip == "10.0.0.2"  # old dup dropped


def test_remap_records_note(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.record_host(HostFact(ip="10.0.0.1"))
    kb.remap_address("10.0.0.1", "10.0.0.2")
    notes = kb.get_notes()
    assert any("10.0.0.1" in n.body and "10.0.0.2" in n.body for n in notes)
```

(If `get_notes()` has a different name, grep `store.py` for the notes accessor and adjust; the
note assertion is the only place it's used.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_kb_remap.py -v`
Expected: FAIL — `AttributeError: 'KB' object has no attribute 'remap_address'`.

- [ ] **Step 3: Implement**

Read `store.py` first to match the exact column lists of `hosts` and `services` and the
`record_note`/connection helpers. Add this method to the `KB` class (uses `self._connect()`,
`self.target_id`):

```python
    def remap_address(self, old_ip: str, new_ip: str) -> dict:
        """Rewrite IP-keyed rows from old_ip to new_ip within this target.

        Handles PK conflicts (hosts PK is (target_id, ip); services PK is
        (target_id, host_ip, port, proto)) by merging/keeping the new-IP row and
        dropping the stale old-IP duplicate. Records a note. Returns counts.
        """
        if old_ip == new_ip:
            return {"hosts": 0, "services": 0, "cred_results": 0}
        hosts = services = creds = 0
        with self._connect() as conn:
            conn.execute("BEGIN")
            # ── hosts ──
            new_host = conn.execute(
                "SELECT 1 FROM hosts WHERE target_id=? AND ip=?",
                (self.target_id, new_ip),
            ).fetchone()
            if new_host is None:
                cur = conn.execute(
                    "UPDATE hosts SET ip=? WHERE target_id=? AND ip=?",
                    (new_ip, self.target_id, old_ip),
                )
                hosts = cur.rowcount
            else:
                # merge: COALESCE new-row nulls from the old row, then delete old
                conn.execute(
                    """
                    UPDATE hosts SET
                        hostname    = COALESCE(hostname, (SELECT hostname    FROM hosts WHERE target_id=? AND ip=?)),
                        os          = COALESCE(os,       (SELECT os          FROM hosts WHERE target_id=? AND ip=?)),
                        domain      = COALESCE(domain,   (SELECT domain      FROM hosts WHERE target_id=? AND ip=?)),
                        smb_signing = COALESCE(smb_signing, (SELECT smb_signing FROM hosts WHERE target_id=? AND ip=?))
                    WHERE target_id=? AND ip=?
                    """,
                    (self.target_id, old_ip) * 4 + (self.target_id, new_ip),
                )
                cur = conn.execute(
                    "DELETE FROM hosts WHERE target_id=? AND ip=?",
                    (self.target_id, old_ip),
                )
                hosts = cur.rowcount
            # ── services ──
            old_svcs = conn.execute(
                "SELECT port, proto FROM services WHERE target_id=? AND host_ip=?",
                (self.target_id, old_ip),
            ).fetchall()
            for port, proto in old_svcs:
                exists = conn.execute(
                    "SELECT 1 FROM services WHERE target_id=? AND host_ip=? AND port=? AND proto=?",
                    (self.target_id, new_ip, port, proto),
                ).fetchone()
                if exists is None:
                    conn.execute(
                        "UPDATE services SET host_ip=? WHERE target_id=? AND host_ip=? AND port=? AND proto=?",
                        (new_ip, self.target_id, old_ip, port, proto),
                    )
                else:
                    conn.execute(
                        "DELETE FROM services WHERE target_id=? AND host_ip=? AND port=? AND proto=?",
                        (self.target_id, old_ip, port, proto),
                    )
                services += 1
            # ── cred_results.target_host ──
            cur = conn.execute(
                "UPDATE cred_results SET target_host=? "
                "WHERE target_host=? AND cred_id IN "
                "(SELECT id FROM credentials WHERE target_id=?)",
                (new_ip, old_ip, self.target_id),
            )
            creds = cur.rowcount
            conn.commit()
        self.record_note(
            f"Refocused {old_ip} -> {new_ip}; remapped {hosts} host(s), "
            f"{services} service(s), {creds} cred-result(s)."
        )
        return {"hosts": hosts, "services": services, "cred_results": creds}
```

(Adjust the `hosts` merge column list to the table's real columns from `schema.py` — currently
`hostname, os, domain, is_dc, smb_signing`. Include `is_dc` in the COALESCE set if present.
`record_note` is the existing notes writer — confirm its name.)

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_kb_remap.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_remap.py
git commit -m "feat(kb): KB.remap_address rewrites host/service rows old->new IP"
```

---

## Task 2: `/etc/hosts` rewrite helper

**Files:**
- Create: `src/reverser/refocus.py`
- Test: `tests/test_refocus_etc_hosts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_refocus_etc_hosts.py`:

```python
from reverser.refocus import rewrite_hosts_entry


def test_rewrite_updates_existing_line(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("127.0.0.1 localhost\n10.0.0.1 box.htb admin.box.htb\n")
    changed = rewrite_hosts_entry(str(p), "box.htb", "10.0.0.1", "10.0.0.2")
    assert changed is True
    assert "10.0.0.2 box.htb admin.box.htb" in p.read_text()
    assert "10.0.0.1 box.htb" not in p.read_text()


def test_rewrite_adds_line_when_missing(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("127.0.0.1 localhost\n")
    changed = rewrite_hosts_entry(str(p), "box.htb", None, "10.0.0.2")
    assert changed is True
    assert "10.0.0.2 box.htb" in p.read_text()


def test_rewrite_noop_when_already_correct(tmp_path):
    p = tmp_path / "hosts"
    p.write_text("10.0.0.2 box.htb\n")
    changed = rewrite_hosts_entry(str(p), "box.htb", "10.0.0.1", "10.0.0.2")
    assert changed is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_refocus_etc_hosts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.refocus'`.

- [ ] **Step 3: Implement**

Create `src/reverser/refocus.py`:

```python
"""Refocus a target onto a new IP: promote a new address, remap KB rows, and
optionally update /etc/hosts."""

from __future__ import annotations

import re


def rewrite_hosts_entry(path: str, hostname: str, old_ip: str | None, new_ip: str) -> bool:
    """Point `hostname` at `new_ip` in a hosts file. Returns True if changed.

    Rewrites any line whose host column list contains `hostname` to use `new_ip`;
    if no such line exists, appends `new_ip hostname`. Pure file operation — the
    caller decides whether to run it under sudo.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []

    changed = False
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        parts = stripped.split()
        ip, names = parts[0], parts[1:]
        if hostname in names:
            found = True
            if ip != new_ip:
                out.append(" ".join([new_ip, *names]))
                changed = True
            else:
                out.append(line)
        else:
            out.append(line)
    if not found:
        out.append(f"{new_ip} {hostname}")
        changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    return changed
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_refocus_etc_hosts.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/refocus.py tests/test_refocus_etc_hosts.py
git commit -m "feat(refocus): /etc/hosts hostname->IP rewrite helper"
```

---

## Task 3: `refocus_target()` core + `RefocusResult`

**Files:**
- Modify: `src/reverser/refocus.py`
- Test: `tests/test_refocus.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_refocus.py`:

```python
import pytest

from reverser.refocus import refocus_target, RefocusResult


def _make_target(tmp_path, monkeypatch, name="box", ip="10.0.0.1"):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.targets import create_target  # or the actual creator — confirm name
    return create_target(name=name, kind="network", initial_address=ip)


def test_refocus_promotes_new_address_and_remaps(tmp_path, monkeypatch):
    from reverser.kb.store import KB, HostFact
    t = _make_target(tmp_path, monkeypatch)
    KB("box").record_host(HostFact(ip="10.0.0.1", hostname="box.htb"))
    res = refocus_target("box", "10.0.0.2")
    assert isinstance(res, RefocusResult)
    assert res.old_ip == "10.0.0.1" and res.new_ip == "10.0.0.2"
    assert res.rows_remapped["hosts"] == 1
    from reverser.targets import load_target
    assert load_target("box").primary_address.value == "10.0.0.2"


def test_refocus_same_ip_is_noop(tmp_path, monkeypatch):
    _make_target(tmp_path, monkeypatch)
    res = refocus_target("box", "10.0.0.1")
    assert res.old_ip == "10.0.0.1" and res.new_ip == "10.0.0.1"
    assert res.rows_remapped == {"hosts": 0, "services": 0, "cred_results": 0}


def test_refocus_reuses_existing_address(tmp_path, monkeypatch):
    from reverser.targets import add_address, load_target
    t = _make_target(tmp_path, monkeypatch)
    add_address(t, "10.0.0.2", "ip")  # already in history, not primary
    refocus_target("box", "10.0.0.2")
    t2 = load_target("box")
    assert t2.primary_address.value == "10.0.0.2"
    assert sum(1 for a in t2.addresses if a.value == "10.0.0.2") == 1  # not duplicated


def test_refocus_out_of_scope_aborts(tmp_path, monkeypatch):
    from reverser.refocus import RefocusScopeError
    _make_target(tmp_path, monkeypatch)
    scope_dir = tmp_path / "box"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["10.0.0.0/29"]\n'  # .0-.7 only; .50 is out
    )
    with pytest.raises(RefocusScopeError):
        refocus_target("box", "10.0.0.50")
    # force overrides
    res = refocus_target("box", "10.0.0.50", force_scope=True)
    assert res.scope_warning is not None
```

(Confirm the target-creation function name — grep `targets.py` for `create_target`/`new_target`/
`for_name`. Confirm the scope.toml directory is `targets/<normalized-name>/scope.toml`.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_refocus.py -v`
Expected: FAIL — `ImportError: cannot import name 'refocus_target'`.

- [ ] **Step 3: Implement**

Append to `src/reverser/refocus.py`:

```python
from dataclasses import dataclass
from typing import Optional


class RefocusError(RuntimeError):
    """Refocus could not be performed."""


class RefocusScopeError(RefocusError):
    """The new IP is out of scope and force was not set."""


@dataclass
class RefocusResult:
    target: str
    old_ip: str
    new_ip: str
    rows_remapped: dict
    hostname_updated: bool
    scope_warning: Optional[str]
    session_refocused: bool = False
    new_address_id: Optional[str] = None


def refocus_target(
    target_name: str,
    new_ip: str,
    *,
    update_etc_hosts: bool = False,
    hostname: Optional[str] = None,
    hosts_path: str = "/etc/hosts",
    force_scope: bool = False,
) -> RefocusResult:
    """Re-point a target at new_ip: promote the address, remap KB rows, optionally
    update /etc/hosts. Does NOT touch any running session (callers do that)."""
    from .targets import load_target, save_target, add_address, set_primary
    from .kb.store import KB
    from .kb.scope import load_scope, ScopeError

    new_ip = (new_ip or "").strip()
    if not new_ip:
        raise RefocusError("new_ip must be a non-empty string")

    target = load_target(target_name)
    old_ip = target.primary_address.value

    if new_ip == old_ip:
        return RefocusResult(
            target=target.name, old_ip=old_ip, new_ip=new_ip,
            rows_remapped={"hosts": 0, "services": 0, "cred_results": 0},
            hostname_updated=False, scope_warning=None,
            new_address_id=target.primary_address_id,
        )

    # ── scope ──
    scope_warning = None
    scope = load_scope(target.name)
    if scope is not None and not scope.is_target_in_scope(new_ip):
        msg = f"{new_ip} is out of scope per scope.toml"
        if not force_scope:
            raise RefocusScopeError(msg)
        scope_warning = msg + " (applied with force_scope)"

    # ── address: reuse existing record or add a new one, then promote ──
    existing = next((a for a in target.addresses if a.value == new_ip), None)
    if existing is not None:
        target = set_primary(target, existing.id)
        new_address_id = existing.id
    else:
        target = add_address(target, new_ip, "ip", label="refocus", make_primary=True)
        new_address_id = target.primary_address_id

    # ── KB remap ──
    rows = KB(target.name).remap_address(old_ip, new_ip)

    # ── /etc/hosts (best-effort) ──
    hostname_updated = False
    if update_etc_hosts and hostname:
        try:
            hostname_updated = rewrite_hosts_entry(hosts_path, hostname, old_ip, new_ip)
        except OSError:
            hostname_updated = False  # caller surfaces the manual line

    return RefocusResult(
        target=target.name, old_ip=old_ip, new_ip=new_ip,
        rows_remapped=rows, hostname_updated=hostname_updated,
        scope_warning=scope_warning, new_address_id=new_address_id,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_refocus.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/refocus.py tests/test_refocus.py
git commit -m "feat(refocus): refocus_target core (address promote + KB remap + scope)"
```

---

## Task 4: `AgentSession.refocus_address`

**Files:**
- Modify: `src/reverser/agent_session.py`
- Test: `tests/test_session_resume.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_session_resume.py` (reuse the snapshot/resume helpers already imported there):

```python
def test_session_refocus_address_updates_target_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.targets import create_target  # confirm creator name

    t = create_target(name="box", kind="network", initial_address="10.0.0.1")
    sess = AgentSession.from_target(t, profile=get_profile("pentest"))

    from reverser.targets import add_address, load_target
    t = add_address(load_target("box"), "10.0.0.2", "ip", make_primary=True)
    new_addr = t.primary_address

    sess.refocus_address(new_addr)
    assert sess.target == "10.0.0.2"
    assert sess.active_address.value == "10.0.0.2"
    assert sess._snapshot.active_address_id == new_addr.id
```

(Confirm `AgentSession.from_target` requires `REVERSER_PENTEST_AUTHORIZED` — if so add
`monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")`. Confirm the creator name.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_session_resume.py -k refocus_address -v`
Expected: FAIL — `AttributeError: 'AgentSession' object has no attribute 'refocus_address'`.

- [ ] **Step 3: Implement**

Add to `AgentSession` in `src/reverser/agent_session.py` (read the class first; reuse its snapshot
save import — `from .sessions import save as save_snapshot` is used elsewhere in the file):

```python
    def refocus_address(self, new_address) -> str:
        """Re-point this live session at a new address. Returns a human note.

        Updates the active address + the legacy engagement string + the snapshot,
        so subsequent tool calls / dispatches use the new IP and a resume stays on it.
        """
        from .sessions import save as save_snapshot

        old = self.target
        self.active_address = new_address
        self.target = new_address.value
        if self.target_obj is not None:
            # keep the in-memory target's primary pointer consistent
            self.target_obj = dataclasses.replace(
                self.target_obj, primary_address_id=new_address.id
            )
        if self._snapshot is not None:
            self._snapshot.active_address_id = new_address.id
            try:
                save_snapshot(self._snapshot)
            except Exception:
                pass
        note = (
            f"Engagement refocused: target is now {new_address.value} (was {old}). "
            f"Use {new_address.value} for all subsequent tool calls."
        )
        return note
```

(Ensure `import dataclasses` is present at the top of the file; add it if not. If `target_obj` is
None on a session, the `dataclasses.replace` block is skipped by the guard.)

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_session_resume.py -k refocus_address -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/agent_session.py tests/test_session_resume.py
git commit -m "feat(session): AgentSession.refocus_address re-points a live engagement"
```

---

## Task 5: `kb_refocus_target` agent tool

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Test: `tests/test_kb_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kb_tools.py` (match the file's auth/target fixture convention used by the
other kb-tool tests; `authorized_target` is a placeholder for that fixture/value):

```python
import pytest
from reverser.tools.kb import kb_refocus_target


@pytest.mark.asyncio
async def test_kb_refocus_target_promotes_and_remaps(authorized_target):
    # authorized_target sets up a network target named/addressed at a known IP;
    # if the fixture creates the target by IP, adapt new_ip accordingly.
    res = await kb_refocus_target({"target": authorized_target, "new_ip": "10.0.0.222"})
    assert res.get("is_error") is not True
    text = res["content"][0]["text"]
    assert "10.0.0.222" in text
    from reverser.targets import load_target
    assert load_target(authorized_target).primary_address.value == "10.0.0.222"


@pytest.mark.asyncio
async def test_kb_refocus_target_rejects_blank_ip(authorized_target):
    res = await kb_refocus_target({"target": authorized_target, "new_ip": ""})
    assert res.get("is_error") is True
    assert "new_ip" in res["content"][0]["text"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_kb_tools.py -k refocus -v`
Expected: FAIL — `ImportError: cannot import name 'kb_refocus_target'`.

- [ ] **Step 3: Implement**

Add to `src/reverser/tools/kb.py` (reuse existing `_check_auth`, `format_tool_result`,
`format_error`, and the `@tool` decorator already imported):

```python
@tool(
    "kb_refocus_target",
    "Re-point the engagement at a new IP for `target` (e.g. after an HTB reset). "
    "Promotes the new address, remaps host/service KB rows old->new, and refocuses "
    "the current session so subsequent tool calls use the new IP. Optionally updates "
    "/etc/hosts when `hostname` is given and update_etc_hosts is true.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target name/identifier."},
            "new_ip": {"type": "string", "description": "The target's new IP address."},
            "hostname": {"type": "string", "description": "Optional hostname (e.g. box.htb)."},
            "update_etc_hosts": {"type": "boolean", "default": False},
            "force_scope": {"type": "boolean", "default": False},
        },
        "required": ["target", "new_ip"],
    },
)
async def kb_refocus_target(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args.get("target")
    new_ip = (args.get("new_ip") or "").strip()
    if not target:
        return format_error("target is required.")
    if not new_ip:
        return format_error("new_ip is required (the target's new IP address).")
    from ..refocus import refocus_target, RefocusError
    try:
        result = refocus_target(
            target, new_ip,
            update_etc_hosts=bool(args.get("update_etc_hosts", False)),
            hostname=args.get("hostname"),
            force_scope=bool(args.get("force_scope", False)),
        )
    except RefocusError as e:
        return format_error(f"Refocus failed: {e}")
    # refocus the live session if this tool is running inside one for this target
    note = ""
    try:
        from ..sessions import current_session
        sess = current_session.get()
        if sess is not None and getattr(sess, "active_address", None) is not None \
                and result.new_address_id is not None:
            note = sess.refocus_address(sess.active_address.__class__(
                id=result.new_address_id, kind="ip", value=new_ip,
                status="active", added_at=result.new_ip,  # placeholder fields; see note
            ))
    except Exception:
        note = ""
    lines = [
        f"Refocused {result.target}: {result.old_ip} -> {result.new_ip}",
        f"Remapped: {result.rows_remapped}",
    ]
    if result.scope_warning:
        lines.append(f"Scope warning: {result.scope_warning}")
    if result.hostname_updated:
        lines.append("/etc/hosts updated.")
    if note:
        lines.append(note)
    return format_tool_result("\n".join(lines))
```

IMPORTANT for Step 3: the inline `Address(...)` construction above is a sketch — do NOT fabricate
Address fields. Instead, load the refocused address from the target and pass the real object:

```python
        from ..targets import load_target
        addr = load_target(target).primary_address
        if sess is not None and getattr(sess, "active_address", None) is not None:
            note = sess.refocus_address(addr)
```

Use that real-object version; delete the placeholder construction.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_kb_tools.py -k refocus -v`
Then registry: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/test_tool_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools): kb_refocus_target re-points the engagement at a new IP"
```

---

## Task 6: GUI endpoint `POST /api/targets/{target}/refocus`

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Modify: `src/reverser/gui_service/session_manager.py`
- Test: `tests/gui_service/test_refocus_route.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gui_service/test_refocus_route.py` (mirror the fixture/app pattern from
`tests/gui_service/test_session_log_replay.py` — `create_app(ServiceConfig(...))`, httpx
`AsyncClient`, `HEADERS = {"Authorization": "Bearer t"}`):

```python
import json
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig

HEADERS = {"Authorization": "Bearer t"}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_refocus_route_changes_primary(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser.targets import create_target
    create_target(name="box", kind="network", initial_address="10.0.0.1")
    r = await client.post("/api/targets/box/refocus",
                          headers=HEADERS, json={"new_ip": "10.0.0.2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["old_ip"] == "10.0.0.1" and body["new_ip"] == "10.0.0.2"
    from reverser.targets import load_target
    assert load_target("box").primary_address.value == "10.0.0.2"


@pytest.mark.asyncio
async def test_refocus_route_unknown_target_404(client):
    r = await client.post("/api/targets/nope/refocus",
                          headers=HEADERS, json={"new_ip": "10.0.0.2"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/gui_service/test_refocus_route.py -v`
Expected: FAIL — 404/405 (route not defined).

- [ ] **Step 3: Implement**

In `session_manager.py`, add a method to refocus the active session if it matches the target:

```python
    def refocus_active(self, target_name: str, new_address) -> bool:
        """If the active session is for target_name, re-point it. Returns True if so."""
        sess = self.active
        if sess is None:
            return False
        if getattr(sess, "target_name", None) not in (None, target_name) \
                and getattr(sess, "target_obj", None) is not None \
                and sess.target_obj.name != target_name:
            return False
        sess.refocus_address(new_address)
        return True
```

(Adjust the match to however the GUISession exposes its target name — confirm by reading the class;
the goal is "only refocus the session if it belongs to this target".)

In `routes/targets.py`, add the endpoint (follow the existing `load_target`/`HTTPException(404)`
pattern; import the refocus core and the request model):

```python
from pydantic import BaseModel as _BaseModel  # if not already imported

class _RefocusBody(_BaseModel):
    new_ip: str
    hostname: str | None = None
    update_etc_hosts: bool = False
    force_scope: bool = False


@router.post("/api/targets/{target}/refocus")
async def refocus_target_route(target: str, body: _RefocusBody, request: Request):
    import reverser.targets as tmod
    from reverser.refocus import refocus_target as _refocus, RefocusError, RefocusScopeError
    try:
        tmod.load_target(target)
    except Exception:
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    try:
        result = _refocus(
            target, body.new_ip,
            update_etc_hosts=body.update_etc_hosts,
            hostname=body.hostname,
            force_scope=body.force_scope,
        )
    except RefocusScopeError as e:
        raise HTTPException(409, detail=str(e))  # 409 -> UI shows the force option
    except RefocusError as e:
        raise HTTPException(400, detail=str(e))
    # refocus the active session if it belongs to this target
    session_refocused = False
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is not None:
        addr = tmod.load_target(target).primary_address
        session_refocused = mgr.refocus_active(target, addr)
    return {
        "target": result.target, "old_ip": result.old_ip, "new_ip": result.new_ip,
        "rows_remapped": result.rows_remapped, "hostname_updated": result.hostname_updated,
        "scope_warning": result.scope_warning, "session_refocused": session_refocused,
    }
```

(Confirm how the app exposes the SessionManager — grep for `session_manager` / `app.state` in
`gui_service/app.py`. Confirm `Request` is imported from fastapi in this module.)

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest tests/gui_service/test_refocus_route.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py src/reverser/gui_service/session_manager.py tests/gui_service/test_refocus_route.py
git commit -m "feat(gui): POST /api/targets/{target}/refocus endpoint + active-session refocus"
```

---

## Task 7: Frontend refocus control

**Files:**
- Modify: `desktop/renderer/src/api/client.ts` (API method)
- Modify: target detail/list component (confirm: `pages/TargetOverview.tsx` and/or `layout/TargetsPane.tsx`)
- Test: a vitest test next to the component or in `desktop/renderer/src/state/`

- [ ] **Step 1: Write the failing test**

First grep `desktop/renderer/src` for the target detail view and the API client shape. Then add a
vitest test for the new client method (mirror existing `api/client.ts` tests if present) asserting it
POSTs to `/api/targets/<name>/refocus` with `{ new_ip }` and returns the parsed result. If the client
is thin/untested, instead add a component test for the refocus form: rendering it, entering an IP,
submitting, and asserting the client method is called with the right args (mock the client).

Example (adapt to the real client/test util):

```typescript
import { describe, it, expect, vi } from "vitest";
import { refocusTarget } from "../api/client";

describe("refocusTarget", () => {
  it("POSTs new_ip to the refocus endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ old_ip: "10.0.0.1", new_ip: "10.0.0.2" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const res = await refocusTarget("box", { new_ip: "10.0.0.2" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/targets/box/refocus"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(res.new_ip).toBe("10.0.0.2");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run (from `desktop/`): `node_modules/.bin/vitest run --root renderer <test-file>`
Expected: FAIL — `refocusTarget` not exported.

- [ ] **Step 3: Implement**

Add `refocusTarget(name, body)` to `api/client.ts` following the existing request helper/auth-header
pattern in that file (do not hand-roll fetch if the file already has a `request()` wrapper — use it).
Then add a small "Refocus / Change IP" control to the target detail view: an input for the new IP, an
optional "update /etc/hosts" checkbox (shown only when the target has a known hostname), a submit that
calls `refocusTarget` and shows the result summary, and — if the call returns 409 (scope) — reveal a
"force" checkbox and retry with `force_scope: true`. Refresh the target/KB view on success.

- [ ] **Step 4: Run to verify it passes**

Run (from `desktop/`): `node_modules/.bin/vitest run --root renderer` (full renderer suite)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src
git commit -m "feat(gui): refocus/change-IP control + refocusTarget API client"
```

---

## Task 8: Full regression

**Files:** none new — verification only.

- [ ] **Step 1: Full Python suite**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -m pytest -q`
Expected: all green (≤1 skipped). Fix any pre-existing test that constructed a target/host and is
affected by `remap_address`/new tool (none expected).

- [ ] **Step 2: Tool registry builds**

Run: `PYTHONPATH="$PWD/src" .../.venv/bin/python -c "from reverser.tools import ALL_TOOLS; print(len(ALL_TOOLS))"`
Expected: count is one greater than before (kb_refocus_target registered); no import error.

- [ ] **Step 3: Full frontend suite**

Run (from `desktop/`): `node_modules/.bin/vitest run --root renderer`
Expected: all green.

- [ ] **Step 4: Commit (if any cleanup)**

```bash
git add -A
git commit -m "test: full regression green for refocus-target-IP"
```

---

## Self-Review notes

- **Spec coverage:** core refocus (Task 3), KB remap old→new with conflict handling (Task 1),
  /etc/hosts opt-in best-effort (Task 2), scope abort + force (Task 3), live-session refocus
  (Task 4), agent tool (Task 5), GUI endpoint + active-session refocus (Task 6), GUI control
  (Task 7). All spec sections map to a task.
- **Confirm-before-editing** (flagged inline, do these during TDD): the target-creation function name
  (`create_target` vs other), the `record_note`/`get_notes` accessor names, the `hosts` table column
  set for the merge COALESCE, whether `AgentSession.from_target` needs `REVERSER_PENTEST_AUTHORIZED`,
  how the GUISession exposes its target name for `refocus_active`, how the app exposes the
  SessionManager (`app.state`), and the renderer target-view filenames + api/client request helper.
- **Type consistency:** `RefocusResult` fields (`target/old_ip/new_ip/rows_remapped/hostname_updated/
  scope_warning/session_refocused/new_address_id`) are used consistently across the tool, endpoint,
  and tests. `remap_address` returns `{"hosts","services","cred_results"}` used in Task 3/5/6.
