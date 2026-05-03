# Plan 5 — AD Profile, Prompts, and Smoke Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the user-facing AD capability: register the new `ad` profile (skill set + system prompt) so users can launch `reverser i -p ad <target>`, augment the existing `pentest` profile with an AD-detection paragraph so generic pentest sessions hand off cleanly, ship the optional `targets/<target>/scope.toml` enforcement layer that any active tool can consult, and provide a manual HTB-AD smoke-test checklist that proves the full stack from Plans 1–4 works end-to-end.

**Architecture:** The `ad` profile is registered in `src/reverser/profiles.py` alongside the existing eleven profiles (`general`, `linux`, `windows`, `android`, `chrome`, `managed`, `api`, `pentest`, `ctf`, `webpentest`, `webapi`, `webrecon`); its `system_addendum` is a hypothesis-driven engagement prompt that names every Plan-2/3/4 tool by name. The optional scope file is parsed by a new `src/reverser/kb/scope.py` module (~120 LoC) that exposes `load_scope(target) -> Scope | None`; if no `scope.toml` is present, the loader returns `None` and active tools behave exactly as before. When a `Scope` is present, individual tools call `scope.assert_in_scope(ip)` / `scope.assert_spray_allowed()` at the top of their handlers. No global enforcement decorator is introduced in this plan — a follow-up roadmap item ("scope envelope") will hoist this into a cross-cutting concern.

**Tech Stack:** Python 3.11+, `tomllib` (stdlib), `ipaddress` (stdlib), `zoneinfo` (stdlib for timezone parsing). Depends on Plans 1–4 (KB foundation, KB read tools + parsers, NetExec tools, BloodHound stack).

**Spec reference:** `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` § Profile + authorization model.

---

## File Structure

**Created:**
- `src/reverser/kb/scope.py` — `Scope` dataclass, `load_scope()`, scope errors
- `tests/test_kb_scope.py` — scope loader and enforcement tests
- `tests/manual/__init__.py` — empty (so pytest does not pick up the manual dir)
- `tests/manual/ad_smoke.md` — manual HTB AD walkthrough checklist

**Modified:**
- `src/reverser/kb/__init__.py` — re-export `load_scope`, `Scope`, `ScopeError`
- `src/reverser/profiles.py` — register `ad` profile + 11 AD-specific skills
- `src/reverser/prompts.py` — add `AD_SYSTEM_PROMPT` constant (referenced from profile addendum) + `AD_PENTEST_PROMPT_TEMPLATE`; augment the existing `pentest` profile addendum in `profiles.py`
- `src/reverser/tools/netexec.py` — call `scope.assert_in_scope(target)` and `scope.assert_spray_allowed()` at the top of each NetExec tool
- `src/reverser/tools/bloodhound.py` — call `scope.assert_in_scope(dc_ip)` at the top of `bloodhound_collect`
- `devenv.nix` — verify `neo4j`, `netexec`, `bloodhound-python` (Python), `neo4j` (Python driver) are present; bump if any is missing
- `README.md` — add the `ad` profile row to the profiles table
- `harness.toml` — verify Incus memory headroom for Neo4j (note: existing `incus/profile.yaml` already sets `limits.memory: 32GB` so no change needed; this task is a no-op confirmation)

---

## Task 1: Scope dataclass + loader (TDD)

**Files:**
- Create: `src/reverser/kb/scope.py`
- Create: `tests/test_kb_scope.py`

- [ ] **Step 1: Write failing tests `tests/test_kb_scope.py`**

```python
"""Tests for the optional per-target scope.toml loader and enforcement."""

import os
from pathlib import Path

import pytest

from reverser.kb.scope import (
    Scope,
    ScopeError,
    load_scope,
)


def test_load_scope_missing_file_returns_none(tmp_targets_dir):
    """When no scope.toml exists, load_scope returns None (no enforcement)."""
    assert load_scope("10.10.10.5") is None


def test_load_scope_basic(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        'out_of_scope_ips = ["10.10.10.5"]\n'
        'allowed_hours = "08:00-18:00 America/New_York"\n'
        "no_dos = true\n"
        "no_account_lockout = true\n"
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.in_scope_cidrs == ["10.10.10.0/24"]
    assert scope.out_of_scope_ips == ["10.10.10.5"]
    assert scope.no_dos is True
    assert scope.no_account_lockout is True


def test_load_scope_minimal(tmp_targets_dir):
    """A scope.toml with just one field should load without errors."""
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope is not None
    assert scope.in_scope_cidrs == ["10.10.10.0/24"]
    assert scope.out_of_scope_ips == []
    assert scope.no_dos is False
    assert scope.no_account_lockout is False


def test_load_scope_invalid_toml_raises(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text("this is not = valid toml [\n")
    with pytest.raises(ScopeError) as exc_info:
        load_scope("10.10.10.5")
    assert "scope.toml" in str(exc_info.value)


def test_is_target_in_scope_inside_cidr(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24", "192.168.1.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope.is_target_in_scope("10.10.10.42") is True
    assert scope.is_target_in_scope("192.168.1.10") is True
    assert scope.is_target_in_scope("172.16.0.1") is False


def test_is_target_in_scope_excluded_ip(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        'out_of_scope_ips = ["10.10.10.42"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope.is_target_in_scope("10.10.10.41") is True
    assert scope.is_target_in_scope("10.10.10.42") is False  # explicitly excluded


def test_is_target_in_scope_empty_cidr_list_allows_all(tmp_targets_dir):
    """No in_scope_cidrs means no positive constraint — only the exclusion list applies."""
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'out_of_scope_ips = ["1.2.3.4"]\n'
    )
    scope = load_scope("10.10.10.5")
    assert scope.is_target_in_scope("172.16.0.1") is True
    assert scope.is_target_in_scope("1.2.3.4") is False


def test_assert_in_scope_passes(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    scope.assert_in_scope("10.10.10.42")  # should not raise


def test_assert_in_scope_raises_for_out_of_scope(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    with pytest.raises(ScopeError) as exc_info:
        scope.assert_in_scope("172.16.0.1")
    assert "out of scope" in str(exc_info.value).lower()
    assert "172.16.0.1" in str(exc_info.value)


def test_assert_spray_allowed_default(tmp_targets_dir):
    """no_account_lockout=False (default) means spray is allowed."""
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("10.10.10.5")
    scope.assert_spray_allowed()  # should not raise


def test_assert_spray_allowed_blocked(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        "no_account_lockout = true\n"
    )
    scope = load_scope("10.10.10.5")
    with pytest.raises(ScopeError) as exc_info:
        scope.assert_spray_allowed()
    assert "lockout" in str(exc_info.value).lower()


def test_assert_dos_allowed_blocked(tmp_targets_dir):
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
        "no_dos = true\n"
    )
    scope = load_scope("10.10.10.5")
    with pytest.raises(ScopeError) as exc_info:
        scope.assert_dos_allowed()
    assert "dos" in str(exc_info.value).lower() or "denial" in str(exc_info.value).lower()


def test_load_scope_normalizes_target_id(tmp_targets_dir):
    """load_scope should accept the same normalized target IDs as for_target."""
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    scope = load_scope("  10.10.10.5  ")
    assert scope is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_scope.py -v`
Expected: 13 errors with `ImportError: cannot import name 'Scope' from 'reverser.kb.scope'`.

- [ ] **Step 3: Implement `src/reverser/kb/scope.py`**

```python
"""Optional per-target scope.toml loader + enforcement helpers.

If `targets/<target>/scope.toml` exists, the active tools should call
`load_scope(target)` and consult the returned `Scope` object before doing
anything that touches the network. If no scope.toml exists, `load_scope`
returns None and no enforcement is performed (legacy behavior).

The file format is intentionally tiny:

    [scope]
    in_scope_cidrs       = ["10.10.10.0/24"]
    out_of_scope_ips     = ["10.10.10.5"]
    allowed_hours        = "08:00-18:00 America/New_York"
    no_dos               = true
    no_account_lockout   = true
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .store import normalize_target


class ScopeError(RuntimeError):
    """Raised when a tool action would violate the loaded scope.toml."""


@dataclass
class Scope:
    """Parsed scope.toml contents + enforcement helpers."""

    in_scope_cidrs: list[str] = field(default_factory=list)
    out_of_scope_ips: list[str] = field(default_factory=list)
    allowed_hours: Optional[str] = None
    no_dos: bool = False
    no_account_lockout: bool = False

    def is_target_in_scope(self, ip: str) -> bool:
        """Return True if `ip` is in scope (CIDR match) and not on the exclusion list.

        If `in_scope_cidrs` is empty, no positive CIDR constraint applies — only
        `out_of_scope_ips` is checked.
        """
        if ip in self.out_of_scope_ips:
            return False
        if not self.in_scope_cidrs:
            return True
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            # Not a literal IP (e.g. a hostname or CIDR) — allow it through;
            # downstream tools resolve and re-check as needed.
            return True
        for cidr in self.in_scope_cidrs:
            try:
                if ip_obj in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def assert_in_scope(self, ip: str) -> None:
        """Raise ScopeError if `ip` is out of scope per the loaded scope.toml."""
        if not self.is_target_in_scope(ip):
            raise ScopeError(
                f"target {ip!r} is out of scope per scope.toml "
                f"(in_scope_cidrs={self.in_scope_cidrs}, "
                f"out_of_scope_ips={self.out_of_scope_ips})"
            )

    def assert_spray_allowed(self) -> None:
        """Raise ScopeError if scope.toml forbids any action that risks lockout."""
        if self.no_account_lockout:
            raise ScopeError(
                "credential spraying is forbidden by scope.toml "
                "(no_account_lockout = true). Edit targets/<target>/scope.toml "
                "to enable, or use a single-attempt check_auth instead."
            )

    def assert_dos_allowed(self) -> None:
        """Raise ScopeError if scope.toml forbids DoS-prone operations."""
        if self.no_dos:
            raise ScopeError(
                "this action is forbidden by scope.toml (no_dos = true) — "
                "denial-of-service-prone operations are out of scope for this engagement."
            )


def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


def load_scope(target: str) -> Optional[Scope]:
    """Load `targets/<target>/scope.toml`. Return None if the file does not exist.

    Raises ScopeError if the file exists but cannot be parsed.
    """
    target_id = normalize_target(target)
    path = _targets_root() / target_id / "scope.toml"
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ScopeError(f"failed to load scope.toml at {path}: {e}") from e
    section = data.get("scope", {})
    return Scope(
        in_scope_cidrs=list(section.get("in_scope_cidrs", [])),
        out_of_scope_ips=list(section.get("out_of_scope_ips", [])),
        allowed_hours=section.get("allowed_hours"),
        no_dos=bool(section.get("no_dos", False)),
        no_account_lockout=bool(section.get("no_account_lockout", False)),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_scope.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/scope.py tests/test_kb_scope.py
git commit -m "feat(kb): add optional per-target scope.toml loader and enforcement"
```

---

## Task 2: Re-export scope API from `reverser.kb`

**Files:**
- Modify: `src/reverser/kb/__init__.py`
- Modify: `tests/test_kb_scope.py`

- [ ] **Step 1: Append failing test**

Append to `tests/test_kb_scope.py`:

```python
def test_scope_re_exported_from_package():
    """Public API: `from reverser.kb import load_scope, Scope, ScopeError`."""
    from reverser.kb import load_scope, Scope, ScopeError
    assert callable(load_scope)
    assert isinstance(Scope(), Scope)
    assert issubclass(ScopeError, RuntimeError)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_kb_scope.py::test_scope_re_exported_from_package -v`
Expected: `ImportError: cannot import name 'load_scope' from 'reverser.kb'`.

- [ ] **Step 3: Modify `src/reverser/kb/__init__.py`**

Add to the imports block (after the existing `.authz` import):

```python
from .scope import Scope, ScopeError, load_scope
```

Add to the `__all__` list:

```python
    "Scope",
    "ScopeError",
    "load_scope",
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_kb_scope.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/__init__.py tests/test_kb_scope.py
git commit -m "feat(kb): re-export scope API from reverser.kb package"
```

---

## Task 3: Wire scope checks into NetExec tools

**Files:**
- Modify: `src/reverser/tools/netexec.py`

- [ ] **Step 1: Read the current `netexec.py` to find each tool entrypoint**

Run: `grep -n '^@tool\|^def netexec_\|^async def netexec_' src/reverser/tools/netexec.py`

Each of `netexec_smb`, `netexec_winrm`, `netexec_ldap`, `netexec_mssql`, `netexec_ssh`, `netexec_ftp_wmi` (six tools, registered in Plan 4) needs the same scope-check block at the top of its handler.

- [ ] **Step 2: Add the scope-check block to each NetExec tool**

At the top of each tool function (immediately after the existing `require_pentest_auth()` call from Plan 4), insert:

```python
    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
            if action == "spray":
                scope.assert_spray_allowed()
        except ScopeError as e:
            return {"error": f"scope.toml violation: {e}"}
    # ────────────────────────────────────────────────────────────────
```

Notes:
- Apply to all six tools (`netexec_smb`, `netexec_winrm`, `netexec_ldap`, `netexec_mssql`, `netexec_ssh`, `netexec_ftp_wmi`).
- For `netexec_ftp_wmi`, the spray-block is unreachable (no spray action) — keep the same template for uniformity; the `if action == "spray":` will simply be false.
- For `netexec_mssql`, the `xp_cmdshell` and `query` actions can in theory cause heavy load — guard those with `scope.assert_dos_allowed()` as well:

```python
            if action in ("xp_cmdshell", "query"):
                scope.assert_dos_allowed()
```

- [ ] **Step 3: Add a regression test**

Append to `tests/test_kb_scope.py`:

```python
def test_netexec_smb_respects_scope(tmp_targets_dir, monkeypatch):
    """If scope.toml excludes a target, netexec_smb returns a scope error and never shells out."""
    target_dir = tmp_targets_dir / "172.16.0.1"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")

    # Spy on subprocess to confirm we never invoked the binary
    from reverser.tools import netexec as netexec_mod
    called = []
    monkeypatch.setattr(
        netexec_mod, "_run_nxc",
        lambda *a, **kw: called.append((a, kw)) or {"stdout": "", "stderr": "", "returncode": 0},
        raising=False,
    )

    # Use the underlying function (not the @tool wrapper) — the wrapper signature
    # depends on Plan 4's tool registration plumbing
    import asyncio
    result = asyncio.run(netexec_mod.netexec_smb.__wrapped__(
        target="172.16.0.1", action="check_auth",
        username="x", password="y",
    )) if hasattr(netexec_mod.netexec_smb, "__wrapped__") else \
        netexec_mod.netexec_smb(target="172.16.0.1", action="check_auth",
                                username="x", password="y")

    assert "scope.toml violation" in str(result)
    assert called == []  # subprocess was never invoked
```

If the test cannot be wired cleanly because of how Plan 4 registered the tool decorator, replace it with a direct unit test of the scope check helper used inside the tool — the goal is to assert "out-of-scope target → no subprocess".

- [ ] **Step 4: Run regression test**

Run: `pytest tests/test_kb_scope.py::test_netexec_smb_respects_scope -v`
Expected: 1 passed (or skipped with a clear reason if the harness doesn't expose the wrapped function — note in the commit message).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_kb_scope.py
git commit -m "feat(netexec): consult scope.toml before any active operation"
```

---

## Task 4: Wire scope check into bloodhound_collect

**Files:**
- Modify: `src/reverser/tools/bloodhound.py`

- [ ] **Step 1: Read `tools/bloodhound.py`**

Run: `grep -n 'def bloodhound_' src/reverser/tools/bloodhound.py`

`bloodhound_collect` is the only one that talks to the network (the others manage Neo4j locally). It receives the DC IP as `dc_ip`.

- [ ] **Step 2: Add scope check to `bloodhound_collect`**

At the top of `bloodhound_collect` (after `require_pentest_auth()`), insert:

```python
    # ── Scope enforcement ───────────────────────────────────────────
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(dc_ip)
        except ScopeError as e:
            return {"error": f"scope.toml violation: {e}"}
    # ────────────────────────────────────────────────────────────────
```

- [ ] **Step 3: Append a regression test to `tests/test_kb_scope.py`**

```python
def test_bloodhound_collect_respects_scope(tmp_targets_dir, monkeypatch):
    """bloodhound_collect must refuse if dc_ip is excluded by scope.toml."""
    target_dir = tmp_targets_dir / "corp.local"
    target_dir.mkdir()
    (target_dir / "scope.toml").write_text(
        "[scope]\n"
        'in_scope_cidrs = ["10.10.10.0/24"]\n'
    )
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")

    from reverser.tools import bloodhound as bh
    called = []
    monkeypatch.setattr(
        bh, "_run_bloodhound_python",
        lambda *a, **kw: called.append((a, kw)) or {"stdout": "", "returncode": 0},
        raising=False,
    )

    result = bh.bloodhound_collect.__wrapped__(
        target="corp.local", domain="corp.local",
        dc_ip="172.16.0.1",  # out of scope
        username="jdoe", password="x",
    ) if hasattr(bh.bloodhound_collect, "__wrapped__") else \
        bh.bloodhound_collect(target="corp.local", domain="corp.local",
                              dc_ip="172.16.0.1", username="jdoe", password="x")
    assert "scope.toml violation" in str(result)
    assert called == []
```

- [ ] **Step 4: Run regression test**

Run: `pytest tests/test_kb_scope.py::test_bloodhound_collect_respects_scope -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/bloodhound.py tests/test_kb_scope.py
git commit -m "feat(bloodhound): consult scope.toml before collecting against a DC"
```

---

## Task 5: AD profile skill set in `profiles.py` (part 1 — skill objects)

**Files:**
- Modify: `src/reverser/profiles.py`

- [ ] **Step 1: Append the 11 AD-specific skill objects after `SKILL_PENTEST_WRITEUP`**

In `src/reverser/profiles.py`, find the existing `SKILL_PENTEST_WRITEUP = Skill(` block and insert the following block immediately after it (keep one blank line before and after):

```python
# ── AD-specific skills ─────────────────────────────────────────────

SKILL_AD_INITIAL_RECON = Skill(
    name="Initial recon",
    key="r",
    description="nmap top-1000 + smb_enum + ldap_search anon + nbtscan",
    prompt="Confirm the target IP, domain (if known), and engagement window. Then run, "
           "in parallel: nmap_scan with version detection on top-1000 TCP ports, smb_enum "
           "for shares and SMB security mode, ldap_search with anonymous bind for the root "
           "DSE and any naming contexts, and nbtscan_scan to harvest NetBIOS names and "
           "workgroup. Record everything into the KB (parsers do this automatically). "
           "Then call kb_show to see the merged picture.",
)

SKILL_AD_IDENTIFY_DCS = Skill(
    name="Identify DCs",
    key="d",
    description="kerberos_enum userenum + ldap_search for objectClass=domainDNS",
    prompt="Identify Domain Controllers on the target. Run kerberos_enum with action=userenum "
           "(uses nmap krb5-enum-users) and ldap_search with filter '(objectClass=domainDNS)' "
           "and '(&(objectCategory=computer)(userAccountControl:1.2.840.113556.1.4.803:=8192))'. "
           "Mark any matched hosts as DCs in the KB via kb_add_note (the LDAP parser also flags "
           "is_dc=True automatically when it sees the SERVER_TRUST_ACCOUNT bit). Call kb_list_hosts "
           "to confirm the result.",
)

SKILL_AD_SPRAY = Skill(
    name="Spray known wordlist",
    key="s",
    description="netexec_smb spray (gated by REVERSER_AD_ALLOW_SPRAY)",
    prompt="Run a credential spray against SMB. First call kb_list_creds to see what we already "
           "have — do not re-spray credentials we have already validated or invalidated. Then run "
           "netexec_smb with action=spray, a small username list, and 1–3 known-bad passwords "
           "(e.g. 'Welcome1', 'Password1', '<Domain>2026!'). The tool refuses unless "
           "REVERSER_AD_ALLOW_SPRAY=1 is set; if it is unset, stop and explain to the user why. "
           "REVERSER_SPRAY_MAX caps attempts per user (default 3). Record any new validated cred "
           "via the standard KB write path (the tool does this for you).",
)

SKILL_AD_ASREP = Skill(
    name="AS-REP roast",
    key="a",
    description="kerberos_enum asreproast (anon LDAP for userlist)",
    prompt="Hunt for AS-REP-roastable accounts. First, if no userlist is present, run "
           "ldap_search with anonymous bind and filter "
           "'(&(samAccountType=805306368)(userAccountControl:1.2.840.113556.1.4.803:=4194304))' "
           "to find users with DONT_REQ_PREAUTH. Save that list, then run kerberos_enum with "
           "action=asreproast against the DC. Each returned hash is recorded in the KB as a "
           "credential with kerberos_ticket=<hash> and status=untested, plus an artifact under "
           "loot/. Do NOT crack hashes inside this tool — surface them and tell the user to crack "
           "with hashcat -m 18200 offline.",
)

SKILL_AD_KERBEROAST = Skill(
    name="Kerberoast",
    key="k",
    description="kerberos_enum kerberoast with KB-stored creds",
    prompt="Request TGS tickets for all SPN-bearing accounts. First call kb_list_creds status='valid' "
           "to find a usable domain credential. Then run kerberos_enum with action=kerberoast, passing "
           "the validated cred. Each returned TGS hash is recorded as a credential row with "
           "kerberos_ticket and status=untested, plus an artifact under loot/. Tell the user to crack "
           "offline with hashcat -m 13100. If no valid cred exists yet, fall back to the AS-REP skill first.",
)

SKILL_AD_VALIDATE_CREDS = Skill(
    name="Validate creds everywhere",
    key="v",
    description="netexec_*/check_auth across all KB creds",
    prompt="For every credential in the KB with status in ('untested', 'valid'), test it against "
           "every relevant service we have discovered. First call kb_list_creds and kb_list_services. "
           "Then for each (cred, host:port) pair, dispatch the right netexec_* tool with action=check_auth: "
           "445/tcp → netexec_smb, 5985/5986 → netexec_winrm, 389/636 → netexec_ldap, "
           "1433 → netexec_mssql, 22 → netexec_ssh, 21 → netexec_ftp_wmi (protocol='ftp'). "
           "Run independent checks in parallel. The tools record cred_results into the KB automatically. "
           "When you find a new valid cred, immediately move on to the BloodHound skill from that user.",
)

SKILL_AD_BLOODHOUND_COLLECT = Skill(
    name="Collect BloodHound",
    key="c",
    description="bloodhound_start → collect → status",
    prompt="Stand up the BloodHound graph for this target. Sequence: "
           "1. bloodhound_start(target) — boots Neo4j with data dir under targets/<target>/neo4j/. "
           "2. bloodhound_collect(target, domain, dc_ip, username, password|nt_hash, "
           "collection_methods='Default,LoggedOn'). For stealthier runs use 'DCOnly'. "
           "3. bloodhound_status(target) — confirm the imported counts (Users, Computers, Groups, OUs, GPOs). "
           "If counts are zero, the collector failed silently — re-check creds and DC reachability.",
)

SKILL_AD_FIND_PATHS = Skill(
    name="Find attack paths",
    key="p",
    description="bloodhound_canned shortest_path_to_da, owned_to_high_value",
    prompt="Map our path to Domain Admin. Run bloodhound_canned with query_name='shortest_path_to_da' "
           "first — it shows the cheapest existing path. Then run query_name='owned_to_high_value' "
           "with params={'username': '<owned-user>@<DOMAIN>'} for each currently-validated user. "
           "Also run 'kerberoastable_users' and 'unconstrained_delegation' to surface fresh primitives. "
           "If the canned queries do not answer the question, drop to bloodhound_query with a custom "
           "Cypher snippet (read-only by default). Record promising paths via kb_add_note.",
)

SKILL_AD_DUMP_SECRETS = Skill(
    name="Dump secrets",
    key="m",
    description="netexec_smb sam/lsa/ntds with valid local-admin",
    prompt="Once we have local-admin (or DA equivalent) on a host, dump cached secrets. "
           "Sequence per target host with valid admin cred: "
           "1. netexec_smb action='sam' — local SAM hashes. "
           "2. netexec_smb action='lsa' — LSA secrets and DPAPI keys. "
           "3. netexec_smb action='ntds' — only on a DC; dumps the entire NTDS.dit. This is loud. "
           "Each dump is auto-saved under loot/ and per-hash credentials are recorded as untested. "
           "Confirm with kb_list_creds afterwards. Do NOT crack inside the tool — surface to the user.",
)

SKILL_AD_SHOW = Skill(
    name="Show what we know",
    key="w",
    description="kb_show + kb_list_creds + kb_list_hosts",
    prompt="Stop. Before the next attack, dump everything we have learned so far. Call, in parallel: "
           "kb_show (single-screen overview), kb_list_hosts (full host inventory), and "
           "kb_list_creds (every credential with status). Read the output carefully. State, in two "
           "sentences: (a) the current best hypothesis for the path to DA, (b) the cheapest experiment "
           "that would disconfirm it. Then resume.",
)

SKILL_AD_REPORT = Skill(
    name="Generate report",
    key="g",
    description="kb_export_report",
    prompt="Generate the engagement report. Call kb_export_report(target) — it renders "
           "targets/<target>/report.md from the KB contents (hosts, services, creds, findings, "
           "notes, artifacts) in the same style as pentest_report_10.13.38.23.md. Read the file "
           "back and confirm the executive summary, methodology, and findings are accurate. If "
           "any finding is missing, add it via kb_add_finding and re-run the report.",
)
```

- [ ] **Step 2: Validate the file still imports**

Run: `python -c "import reverser.profiles; print(len(reverser.profiles.PROFILES))"`
Expected: prints `12` (existing 12 profiles — `ad` is not registered yet).

Run: `python -c "from reverser.profiles import SKILL_AD_INITIAL_RECON; print(SKILL_AD_INITIAL_RECON.name)"`
Expected: prints `Initial recon`.

- [ ] **Step 3: Commit**

```bash
git add src/reverser/profiles.py
git commit -m "feat(profiles): add 11 AD-specific skill objects"
```

---

## Task 6: Register the `ad` profile in `profiles.py` (part 2 — profile entry)

**Files:**
- Modify: `src/reverser/profiles.py`

- [ ] **Step 1: Add the AD skills list immediately after `_PENTEST_SKILLS`**

In `src/reverser/profiles.py`, find the `_PENTEST_SKILLS = [` block (around line 272) and append directly below it (keeping the blank line):

```python
_AD_SKILLS = [
    SKILL_AD_INITIAL_RECON,
    SKILL_AD_IDENTIFY_DCS,
    SKILL_AD_SPRAY,
    SKILL_AD_ASREP,
    SKILL_AD_KERBEROAST,
    SKILL_AD_VALIDATE_CREDS,
    SKILL_AD_BLOODHOUND_COLLECT,
    SKILL_AD_FIND_PATHS,
    SKILL_AD_DUMP_SECRETS,
    SKILL_AD_SHOW,
    SKILL_AD_REPORT,
]
```

- [ ] **Step 2: Register the `ad` profile**

Find the existing `_register(Profile(name="Pentest", ...))` block and insert a new `_register(Profile(...))` call directly after it. The system addendum is the full AD prompt — paste it verbatim:

```python
_register(Profile(
    name="Active Directory",
    key="ad",
    description="Internal AD engagement — assumed-breach methodology with NetExec, BloodHound, and KB",
    system_addendum="""\

## Profile: Active Directory Penetration Testing

You are an AD-focused penetration tester. The target is an Active Directory environment. \
Your methodology is **assumed-breach internal engagement**: enumerate → spray → escalate \
via graph → dump → lateral. You have a persistent per-target knowledge base, a full \
NetExec wrapper for every relevant protocol, and a BloodHound stack with canned and \
free-form Cypher.

### Scope confirmation (do this BEFORE the first active tool call)

State, in one sentence each:
1. The target IPs / CIDRs in scope.
2. The target domain (FQDN) — confirm or mark "unknown, will discover".
3. The engagement time window (or "no constraint").
4. Whether spray is allowed (REVERSER_AD_ALLOW_SPRAY) and, if scope.toml exists, what it forbids.

If the user has not provided this and no scope.toml exists, ASK before scanning anything.

### Hypothesis-driven loop (NON-NEGOTIABLE)

Every 5 tool calls, stop and explicitly write down:
- (a) Your current hypothesis about the foothold path to Domain Admin.
- (b) The single cheapest experiment that would disconfirm it.
- (c) What you would pivot to if (b) fails.

Do NOT grind the same primitive past 3 failed attempts. Pivot. The 10.13.38.23 report \
in this repo is what happens when this rule is ignored — ~1700 password attempts, no foothold, \
no lessons retained.

### KB usage (READ before WRITE; RECORD as you go)

Every tool you call writes to the per-target KB at `targets/<target>/state.db`. \
Before each new attack, call `kb_show` and `kb_list_creds` — do NOT re-derive facts you \
already know. The KB is your durable working memory across this session and the next.

Record findings via `kb_add_finding` the moment you confirm them, not at the end. A finding \
that exists only in your context window is a finding that vanishes when the session ends.

### Credential lifecycle (validate everywhere, immediately)

When you discover a valid credential, immediately try it against ldap, winrm, mssql, ssh \
via the corresponding `netexec_*` `check_auth` actions and record each result. Then run \
`bloodhound_canned owned_to_high_value` for that user to plan the next move. A new valid \
cred is the most important event in any AD engagement — treat it that way.

### BloodHound is your map

As soon as you have ANY valid domain credential, run `bloodhound_collect`. Then \
`bloodhound_canned shortest_path_to_da` is your default next move. Use the canned queries \
first (`kerberoastable_users`, `asreproastable_users`, `unconstrained_delegation`, \
`computers_where_user_admin`, `users_with_dcsync`, `owned_to_high_value`, …). Drop to \
`bloodhound_query` with free-form Cypher only when no canned query fits.

### Stop conditions

Stop and write the final report when EITHER:
- Domain Admin is reached. Dump NTDS via `netexec_smb` action=`ntds`, then call `kb_export_report`.
- Three orthogonal attack paths have been exhausted with no progress. Write a finding \
  describing the surface examined, the primitives tried, and the conclusion. Then call \
  `kb_export_report`.

### Tool reference

KB read/write:
- `kb_show`, `kb_list_hosts`, `kb_list_services`, `kb_list_creds`,
- `kb_add_finding`, `kb_add_note`, `kb_export_report`

NetExec (per-protocol; all share `target`, `username`, `password`, `nt_hash`, `domain`):
- `netexec_smb` — actions: shares, users, groups, computers, pass_pol, rid_brute, sam, lsa, ntds, loggedon, sessions, disks, spider, exec, spray, check_auth
- `netexec_winrm` — actions: check_auth, exec, ps, spray
- `netexec_ldap` — actions: check_auth, users, groups, computers, trusts, gmsa, asreproastable, kerberoastable, dc_list, active_users, admin_count, password_not_required
- `netexec_mssql` — actions: check_auth, databases, xp_cmdshell, query, spray
- `netexec_ssh` — actions: check_auth, exec, spray
- `netexec_ftp_wmi` — protocol: ftp|wmi; actions: check_auth, list, get, exec

BloodHound:
- `bloodhound_start`, `bloodhound_stop`, `bloodhound_status`,
- `bloodhound_collect` (wraps bloodhound-python; auto-imports into the per-target Neo4j),
- `bloodhound_canned` (15 canned queries; see spec),
- `bloodhound_query` (free-form Cypher; read-only unless allow_writes=True).

Existing pentest tools that auto-record into the KB:
- `nmap_scan`, `ldap_search`, `kerberos_enum`, `smb_enum`, `nbtscan_scan`, `banner_grab`,
- `whatweb_scan`, `gobuster_scan`, `nikto_scan`, `ssl_scan`.

### Spray safety guardrails (built into the tools, not just the prompt)

- `netexec_*` `spray` actions hard-cap attempts per user at `REVERSER_SPRAY_MAX` (default: 3).
- Spray refuses unless `REVERSER_AD_ALLOW_SPRAY=1` is set.
- If `targets/<target>/scope.toml` sets `no_account_lockout = true`, spray is hard-disabled \
  for that target regardless of env vars.

### CRITICAL RULES

- This is authorized penetration testing. The user has confirmed via `.reverser-authorized` \
  or `REVERSER_PENTEST_AUTHORIZED=1`.
- Do NOT attempt destructive attacks or denial-of-service.
- Do NOT crack hashes inside the tool. Surface them via `kb_add_finding` and `record_artifact` \
  and tell the user to crack offline with hashcat.
- Do NOT invent NetExec module names or canned-query names. If you are unsure, call the tool \
  with no module and read what comes back.
- Do NOT skip the hypothesis-loop. It is the difference between a 30-minute foothold and a \
  3-hour token-burn with nothing to show.
""",
    skills=_AD_SKILLS,
))
```

- [ ] **Step 3: Verify registration**

Run: `python -c "from reverser.profiles import PROFILES, get_profile; print(len(PROFILES)); print(get_profile('ad').name); print([s.name for s in get_profile('ad').skills])"`

Expected:
```
13
Active Directory
['Initial recon', 'Identify DCs', 'Spray known wordlist', 'AS-REP roast', 'Kerberoast', 'Validate creds everywhere', 'Collect BloodHound', 'Find attack paths', 'Dump secrets', 'Show what we know', 'Generate report']
```

- [ ] **Step 4: Add a regression test**

Create `tests/test_profiles_ad.py`:

```python
"""Regression tests for the AD profile registration."""

import pytest

from reverser.profiles import PROFILES, get_profile, list_profiles


def test_ad_profile_registered():
    assert "ad" in PROFILES
    p = get_profile("ad")
    assert p.name == "Active Directory"
    assert "assumed-breach" in p.system_addendum.lower()


def test_ad_profile_has_all_eleven_skills():
    p = get_profile("ad")
    assert len(p.skills) == 11
    expected_names = {
        "Initial recon", "Identify DCs", "Spray known wordlist",
        "AS-REP roast", "Kerberoast", "Validate creds everywhere",
        "Collect BloodHound", "Find attack paths", "Dump secrets",
        "Show what we know", "Generate report",
    }
    actual_names = {s.name for s in p.skills}
    assert actual_names == expected_names


def test_ad_profile_skill_keys_unique():
    p = get_profile("ad")
    keys = [s.key for s in p.skills]
    assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"


def test_ad_profile_in_list_profiles():
    keys = {p.key for p in list_profiles()}
    assert "ad" in keys


def test_ad_prompt_mentions_key_tools():
    """The system addendum must enumerate the new tool surface."""
    p = get_profile("ad")
    addendum = p.system_addendum
    for tool in [
        "kb_show", "kb_list_creds", "kb_add_finding", "kb_export_report",
        "netexec_smb", "netexec_winrm", "netexec_ldap", "netexec_mssql",
        "netexec_ssh", "netexec_ftp_wmi",
        "bloodhound_start", "bloodhound_collect", "bloodhound_canned",
        "bloodhound_query",
    ]:
        assert tool in addendum, f"missing tool reference: {tool}"


def test_ad_prompt_mentions_hypothesis_loop():
    p = get_profile("ad")
    assert "hypothesis" in p.system_addendum.lower()
    assert "5 tool calls" in p.system_addendum or "every 5" in p.system_addendum.lower()
```

- [ ] **Step 5: Run regression tests**

Run: `pytest tests/test_profiles_ad.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/profiles.py tests/test_profiles_ad.py
git commit -m "feat(profiles): register ad profile with full assumed-breach system prompt"
```

---

## Task 7: Augment the existing `pentest` profile with the AD-detection paragraph

**Files:**
- Modify: `src/reverser/profiles.py`

- [ ] **Step 1: Find the pentest profile addendum**

In `src/reverser/profiles.py`, locate the `_register(Profile(name="Pentest", key="pentest", ...))` call. Within its `system_addendum` triple-quoted string, find the closing block — the last paragraph reads:

```
IMPORTANT: This is authorized penetration testing. Focus on discovery and enumeration. \
Do not attempt destructive attacks or denial of service.
```

- [ ] **Step 2: Insert the AD-detection paragraph immediately before that IMPORTANT block**

Add this block (one blank line above and below):

```
**AD detection — pivot to the `ad` profile when you see it.**
If during recon you discover SMB (445), LDAP (389/636), or Kerberos (88) — especially with \
a Windows or Domain Controller fingerprint (Server 2016/2019/2022, smb-os-discovery reporting \
a domain, krb5-enum-users responding) — this is an Active Directory environment. Stop the \
generic pentest flow, surface a clear recommendation to the user that they re-run with \
`reverser i -p ad <target>`, and prefer the AD-specific tooling (BloodHound, NetExec, the KB) \
for that target. The `ad` profile has a hypothesis-driven prompt and 11 skills tailored to \
AD engagements — none of that is loaded in the generic `pentest` profile.

```

- [ ] **Step 3: Add a regression test**

Append to `tests/test_profiles_ad.py`:

```python
def test_pentest_profile_mentions_ad_pivot():
    p = get_profile("pentest")
    assert "ad" in p.system_addendum.lower()
    assert "-p ad" in p.system_addendum
    assert "BloodHound" in p.system_addendum or "bloodhound" in p.system_addendum.lower()


def test_pentest_profile_still_loadable_after_augmentation():
    """Smoke test: the augmented pentest profile must still parse."""
    p = get_profile("pentest")
    assert p.name == "Pentest"
    assert len(p.skills) > 0
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_profiles_ad.py -v`
Expected: 8 passed (6 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/profiles.py tests/test_profiles_ad.py
git commit -m "feat(profiles): pentest profile recommends pivot to ad when AD is detected"
```

---

## Task 8: Verify devenv.nix has all required AD packages

**Files:**
- Modify (if needed): `devenv.nix`

- [ ] **Step 1: Inventory current devenv.nix vs required AD packages**

Required by Plans 3–4 (per spec):

| Required | Kind | In devenv.nix today? |
|---|---|---|
| `neo4j` | Native (pkgs) | NO — must add |
| `netexec` | Native (pkgs) | NO — must add (note: package name in nixpkgs is `netexec`) |
| `bloodhound` | Python | NO — must add (package: `bloodhound`, the bloodhound-python collector) |
| `neo4j` | Python driver | NO — must add (package: `neo4j`) |
| `impacket` | Python | YES — already present |
| `krb5` | Native | YES — already present |

Run: `grep -E '^\s*(neo4j|netexec|bloodhound)' devenv.nix`
Expected: zero matches → all four entries need to be added (skip any that earlier plans 3–4 already added).

- [ ] **Step 2: Add missing native packages to the `pkgs` list**

In `devenv.nix`, find the `# Penetration testing / Network recon` block (around `nmap`, `nikto`, `gobuster`, `seclists`) and append two lines so the block looks like:

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
    netexec                # nxc — successor to crackmapexec; AD swiss-army knife
    neo4j                  # graph DB for BloodHound (per-target data dir)
```

- [ ] **Step 3: Add missing Python packages to the venv requirements block**

Find the `requirements = ''` block (~line 127) and append two lines so the tail of the block looks like:

```nix
        impacket
	invoke
	pynacl
	paramiko
	bloodhound
	neo4j
      '';
```

(The leading whitespace uses tabs to match the existing entries — preserve that.)

- [ ] **Step 4: Add to enterTest checks**

In the `enterTest` block, append after the existing `python3 -c "import wafw00f"` line:

```bash
    nxc --version > /dev/null 2>&1 && echo "✓ netexec (nxc)" || echo "✗ netexec"
    neo4j --version > /dev/null 2>&1 && echo "✓ neo4j" || echo "✗ neo4j"
    python3 -c "import bloodhound" > /dev/null 2>&1 && echo "✓ bloodhound (python)" || echo "✗ bloodhound (python)"
    python3 -c "import neo4j" > /dev/null 2>&1 && echo "✓ neo4j (python driver)" || echo "✗ neo4j (python driver)"
```

- [ ] **Step 5: Reload devenv and verify the new tools resolve**

Run (inside the devenv shell): `devenv up || true; devenv shell -- nxc --version && devenv shell -- neo4j --version && devenv shell -- python3 -c "import bloodhound, neo4j; print('ok')"`

Or simply `direnv reload && nxc --version && neo4j --version && python3 -c "import bloodhound, neo4j; print('ok')"` if direnv is wired.

Expected: all three commands succeed.

If `netexec` or `bloodhound` is not present in your nixpkgs channel, fall back to installing the Python form via the venv requirements block only and document the gap with a `# TODO` comment in `devenv.nix`. (The `bloodhound` Python collector covers most workflows; the native `nxc` binary is strongly preferred but not strictly required for early integration.)

- [ ] **Step 6: Commit**

```bash
git add devenv.nix
git commit -m "build(devenv): add neo4j, netexec, bloodhound-python, neo4j driver"
```

---

## Task 9: Verify Incus harness memory headroom for Neo4j

**Files:**
- Read-only verification: `incus/profile.yaml`, `harness.toml`

- [ ] **Step 1: Inspect current Incus memory limit**

Run: `grep -n "memory" incus/profile.yaml`

Expected output:
```
7:  limits.memory: 32GB
8:  limits.memory.swap: "false"
```

The spec mentions a "2G → 4G" bump but the existing `incus/profile.yaml` already provisions **32GB** — vastly more than Neo4j needs. **No change required.**

- [ ] **Step 2: Document the verification with a no-op commit only if a TODO note is needed**

If you want to record the verification (recommended for future-readers), append a comment line to `incus/profile.yaml` immediately after `limits.memory: 32GB`:

```yaml
  # 32GB provides ample headroom for Neo4j (per-target BloodHound data dir),
  # impacket, NetExec, and parallel pentest tools. Reviewed 2026-05-03 (Plan 5).
```

- [ ] **Step 3: Commit (only if the comment was added — otherwise skip this task)**

```bash
git add incus/profile.yaml
git commit -m "docs(incus): note 32GB memory ceiling covers Neo4j (Plan 5 review)"
```

---

## Task 10: Update README.md profiles table

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the `ad` row to the profiles table**

In `README.md`, find the profiles table (begins around line 95 with `| Profile | Key | Description |`). After the `| CTF / Crackme | \`ctf\` | …` row, insert:

```
| Active Directory | `ad` | Internal AD engagement — assumed-breach methodology with NetExec, BloodHound, KB |
```

- [ ] **Step 2: Update the profile mention in the enterShell echo of `devenv.nix`**

In `devenv.nix`, find the line:

```
    echo "  RE Profiles: general linux windows android chrome managed api pentest ctf"
```

Replace with:

```
    echo "  RE Profiles: general linux windows android chrome managed api pentest ad ctf"
```

- [ ] **Step 3: Add a brief AD profile note immediately below the profiles table**

In `README.md`, immediately after the closing of the profiles table (after the `reverser interactive --list-profiles` code block), insert:

```markdown
**Active Directory engagements:** the `ad` profile drives an assumed-breach internal AD methodology. \
It bundles 11 skills (initial recon → DC discovery → AS-REP/Kerberoast → BloodHound graph → NTDS dump \
→ report) backed by per-target persistent state in `targets/<target>/state.db`, NetExec for every \
relevant protocol, and BloodHound (Neo4j + bloodhound-python collector + canned + free-form Cypher). \
Spray actions are gated behind `REVERSER_AD_ALLOW_SPRAY=1`; an optional `targets/<target>/scope.toml` \
file can tighten enforcement further.

```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p ad 10.10.10.5
```
```

- [ ] **Step 4: Verify markdown still parses (no malformed table)**

Run: `grep -c "^|" README.md`
Expected: a non-zero count (the count goes up by 1 vs before).

- [ ] **Step 5: Commit**

```bash
git add README.md devenv.nix
git commit -m "docs(readme): document ad profile and update profiles table"
```

---

## Task 11: Write tests/manual/ad_smoke.md (part 1 — setup + recon)

**Files:**
- Create: `tests/manual/__init__.py`
- Create: `tests/manual/ad_smoke.md`

- [ ] **Step 1: Create the empty `tests/manual/__init__.py`**

Create the file with no contents (so pytest's auto-discovery does not treat the directory as a test package; the `.md` file is documentation).

- [ ] **Step 2: Create `tests/manual/ad_smoke.md` with the setup and recon steps (Steps 1–4)**

```markdown
# Manual smoke test — Active Directory engagement (~30 min)

This walkthrough exercises the full AD capability stack (Plans 1–5) end-to-end against a
known-stable HackTheBox AD lab box. **Do not skip this before declaring the AD feature
complete.** Each numbered step lists the command the LLM should issue and the expected KB
state immediately after — verify both before moving on.

**Recommended boxes (any of):**
- HTB Forest — easiest; AS-REP roastable user, kerberoastable svc account
- HTB Sauna — easy; AS-REP roastable user, AutoLogon credentials in registry
- HTB Active — easy; readable Groups.xml on SYSVOL with cpassword

**Prerequisites (do these BEFORE Step 1):**
- VPN connected; lab box pingable.
- `REVERSER_PENTEST_AUTHORIZED=1` exported in shell.
- `REVERSER_AD_ALLOW_SPRAY=1` exported (only needed for Step 3 if you choose to test spray).
- `direnv` reloaded so `nxc`, `neo4j`, and `bloodhound-python` resolve on PATH.
- Working directory at the repo root (so `targets/` is created here).

---

## Step 1 — Launch reverser with the `ad` profile

Command:
```sh
REVERSER_PENTEST_AUTHORIZED=1 reverser i -p ad <BOX_IP>
```

Expected:
- TUI opens with "Active Directory" profile selected (top bar).
- F1 menu lists 11 AD skills.
- The system prompt section shown via `?` mentions "assumed-breach", "hypothesis-driven loop",
  and the new tool surface (`netexec_*`, `bloodhound_*`, `kb_*`).

KB state: empty (no `targets/<BOX_IP>/` directory yet).

---

## Step 2 — Initial recon

Trigger: F1 → "Initial recon" (or type the prompt manually).

Expected tool calls (in parallel where possible):
- `nmap_scan` with version detection on top-1000 TCP ports.
- `smb_enum` for shares + SMB security mode.
- `ldap_search` with anonymous bind for the root DSE.
- `nbtscan_scan` for NetBIOS names.

KB state after:
- `targets/<BOX_IP>/state.db` exists.
- `kb_list_hosts` returns at least one host (the box IP).
- `kb_list_services` returns at least: 53/tcp (DNS), 88/tcp (Kerberos), 135/tcp (RPC),
  139/tcp (NetBIOS), 389/tcp (LDAP), 445/tcp (SMB), 464/tcp (kpasswd), 593/tcp (RPC over HTTP),
  636/tcp (LDAPS), 3268/tcp (Global Catalog), 3269/tcp (LDAPS GC).
- `targets/<BOX_IP>/state.db` has a populated `services` table; `kb_show` renders without errors.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT host_ip, port, service FROM services ORDER BY port"
```

---

## Step 3 — Identify the Domain Controller and domain name

Trigger: F1 → "Identify DCs".

Expected tool calls:
- `kerberos_enum` action=`userenum` (uses nmap krb5-enum-users).
- `ldap_search` filter `(objectClass=domainDNS)` and the SERVER_TRUST_ACCOUNT bit-test filter.

KB state after:
- The host now has `is_dc=1` in the `hosts` table.
- The `targets` table has the discovered domain in the `domain` column (or a note records it).
- `kb_list_hosts` shows is_dc=True.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT ip, hostname, os, domain, is_dc FROM hosts"
```

If `is_dc` is still 0 here, the LDAP parser missed the SERVER_TRUST_ACCOUNT flag — file a bug.

---

## Step 4 — AS-REP roast

Trigger: F1 → "AS-REP roast".

Expected tool calls:
- `ldap_search` with the DONT_REQ_PREAUTH filter to harvest a userlist (anon LDAP only).
- `kerberos_enum` action=`asreproast` against the DC, supplying the userlist.

KB state after:
- For each AS-REP-roastable user, a row appears in the `credentials` table with:
  - `username` set
  - `kerberos_ticket` populated (the `$krb5asrep$23$...` blob)
  - `status = 'untested'`
  - `source_tool = 'kerberos_enum'`
- An entry in the `artifacts` table with `kind = 'asreproast_hashes'` pointing at a file
  under `targets/<BOX_IP>/loot/`.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db \
  "SELECT username, substr(kerberos_ticket, 1, 30), status FROM credentials WHERE kerberos_ticket IS NOT NULL"
sqlite3 targets/<BOX_IP>/state.db "SELECT kind, path FROM artifacts"
```

For HTB Forest, you should see at least `svc-alfresco`. For Sauna, `fsmith` (after manual user
discovery from the website).
```

- [ ] **Step 3: Verify the file is well-formed**

Run: `wc -l tests/manual/ad_smoke.md`
Expected: ~120+ lines so far.

- [ ] **Step 4: Commit**

```bash
git add tests/manual/__init__.py tests/manual/ad_smoke.md
git commit -m "test(manual): add AD smoke-test checklist (steps 1-4)"
```

---

## Task 12: Append Steps 5–10 to ad_smoke.md (cracking + cred validation + BloodHound)

**Files:**
- Modify: `tests/manual/ad_smoke.md`

- [ ] **Step 1: Append the next block of steps**

Append to `tests/manual/ad_smoke.md`:

```markdown
---

## Step 5 — Crack the AS-REP hashes (manual; outside the agent)

This step is OUT OF SCOPE for the LLM. Drop to your own shell.

```sh
hashcat -m 18200 targets/<BOX_IP>/loot/asreproast_hashes.txt /usr/share/wordlists/rockyou.txt
```

Expected: at least one hash cracks (HTB Forest: `svc-alfresco:s3rvice`).

After cracking, manually record the cleartext into the KB so subsequent steps see it:

```sh
sqlite3 targets/<BOX_IP>/state.db <<SQL
UPDATE credentials
SET password = 's3rvice', status = 'untested'
WHERE username = 'svc-alfresco' AND kerberos_ticket IS NOT NULL;
SQL
```

(In a future plan we will surface a `kb_set_password` helper. For now, this manual
update is the smoke-test compromise.)

KB state after:
- `kb_list_creds` shows the cracked cred with `password` set and `status='untested'`.

---

## Step 6 — Validate the cracked cred against SMB

Trigger: tell the LLM "We cracked `svc-alfresco:s3rvice` — validate it everywhere."

Expected tool call:
- `netexec_smb` action=`check_auth` username=`svc-alfresco` password=`s3rvice` on the box IP.
- Followed by `netexec_winrm`, `netexec_ldap` check_auth in parallel.

KB state after:
- `credentials.status = 'valid'` for `svc-alfresco`.
- `cred_results` has at least one row with `success=1` for the working service.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db \
  "SELECT c.username, cr.service_kind, cr.target_host, cr.success
   FROM credentials c JOIN cred_results cr ON c.id = cr.cred_id"
```

---

## Step 7 — Start BloodHound and collect

Trigger: F1 → "Collect BloodHound".

Expected tool calls (sequential):
- `bloodhound_start(target=<BOX_IP>)` — spins up Neo4j on bolt port 7687 with data dir
  at `targets/<BOX_IP>/neo4j/`.
- `bloodhound_collect(target=<BOX_IP>, domain=<DOMAIN>, dc_ip=<BOX_IP>,
   username='svc-alfresco', password='s3rvice', collection_methods='Default,LoggedOn')`
- `bloodhound_status(target=<BOX_IP>)` — reports node counts.

Expected output of `bloodhound_status`:
- Users: ≥10
- Computers: ≥1
- Groups: ≥10
- OUs: ≥1

KB state after:
- `targets/<BOX_IP>/neo4j/` directory populated with a `data/` subdir.
- A note in the `notes` table recording the imported counts.

Verification:
```sh
ls targets/<BOX_IP>/neo4j/data/
sqlite3 targets/<BOX_IP>/state.db "SELECT body FROM notes ORDER BY id DESC LIMIT 1"
```

---

## Step 8 — Find the shortest path to Domain Admin

Trigger: F1 → "Find attack paths".

Expected tool calls:
- `bloodhound_canned(target=<BOX_IP>, query_name='shortest_path_to_da')`
- `bloodhound_canned(target=<BOX_IP>, query_name='owned_to_high_value', params={'username': 'SVC-ALFRESCO@<DOMAIN>'})`
- `bloodhound_canned(target=<BOX_IP>, query_name='kerberoastable_users')`

For HTB Forest, the canned `shortest_path_to_da` query should reveal the
`Account Operators → Exchange Windows Permissions → DCSync` path.

KB state after:
- A `notes` entry recording the discovered path (LLM should call `kb_add_note` with the result).

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT body FROM notes WHERE body LIKE '%path%' OR body LIKE '%DCSync%'"
```

---

## Step 9 — Validate via LDAP from the same cred

Trigger: tell the LLM "Confirm the cred works against LDAP and dump the user list."

Expected tool calls:
- `netexec_ldap` action=`check_auth`, then action=`users` (or action=`active_users`).

KB state after:
- `cred_results` has a `service_kind='ldap'` row with `success=1`.
- New host rows are recorded for any computers discovered via LDAP enumeration.

---

## Step 10 — (HTB Forest specific) Dump NTDS via DCSync

This step depends on the box. If your chosen box does not have a DCSync path from the
foothold cred, skip and document why.

Trigger: tell the LLM "We have DCSync rights — dump NTDS."

Expected tool call:
- `netexec_smb` action=`ntds` username=… password=… on the DC IP.

KB state after:
- An `artifacts` row with `kind='ntds_dump'` pointing at a file under
  `targets/<BOX_IP>/loot/`.
- Per-extracted credential rows in `credentials` with `nt_hash` populated and
  `status='untested'`.

Verification:
```sh
sqlite3 targets/<BOX_IP>/state.db "SELECT kind, path FROM artifacts WHERE kind = 'ntds_dump'"
sqlite3 targets/<BOX_IP>/state.db \
  "SELECT username, substr(nt_hash, 1, 12) FROM credentials WHERE nt_hash IS NOT NULL LIMIT 10"
```
```

- [ ] **Step 2: Commit**

```bash
git add tests/manual/ad_smoke.md
git commit -m "test(manual): add AD smoke-test steps 5-10 (cracking + bloodhound)"
```

---

## Task 13: Append Steps 11–13 + closeout to ad_smoke.md

**Files:**
- Modify: `tests/manual/ad_smoke.md`

- [ ] **Step 1: Append the final block**

Append to `tests/manual/ad_smoke.md`:

```markdown
---

## Step 11 — Show what we know

Trigger: F1 → "Show what we know".

Expected tool calls (in parallel):
- `kb_show()`
- `kb_list_hosts(target=<BOX_IP>)`
- `kb_list_creds(target=<BOX_IP>)`

Expected output:
- A single-screen overview listing host count, port count, valid creds (count + most
  recent), and finding count by severity.
- Full host inventory with the DC marked `is_dc=True`.
- All credentials with their statuses (`valid` for `svc-alfresco`, `untested` for any
  freshly-dumped NTDS hashes).

LLM should then state, in two sentences, the current foothold-path hypothesis and the
cheapest disconfirming experiment. **If it skips the hypothesis statement, the prompt is
not being followed — file a bug.**

---

## Step 12 — Generate the final report

Trigger: F1 → "Generate report".

Expected tool call:
- `kb_export_report(target=<BOX_IP>)`

Expected output:
- File written to `targets/<BOX_IP>/report.md`.
- Sections present: Executive Summary, Target Information, Discovered Hosts,
  Discovered Services, Credentials, Findings, Notes.

Verification:
```sh
test -f targets/<BOX_IP>/report.md && echo "report exists"
head -40 targets/<BOX_IP>/report.md
```

The report should mention every host, service, and finding present in the KB. If a
finding is missing, the LLM should call `kb_add_finding` and re-run the report.

---

## Step 13 — Stop Neo4j (cleanup)

Trigger: tell the LLM "We're done — clean up."

Expected tool call:
- `bloodhound_stop(target=<BOX_IP>)`

Expected output:
- The Neo4j PID file at `targets/<BOX_IP>/neo4j/.pid` is removed.
- `bloodhound_status(target=<BOX_IP>)` now reports "stopped".

Verification:
```sh
test ! -f targets/<BOX_IP>/neo4j/.pid && echo "neo4j stopped cleanly"
```

---

## Pass criteria

The smoke test passes if:
- All 13 steps executed without hand-editing tool source code.
- Every "KB state after" verification query returned the expected non-empty result.
- The final report includes every host, service, valid credential, and finding observed
  during the engagement.
- The LLM followed the hypothesis-driven loop (you saw at least one explicit hypothesis
  statement in the transcript).
- Total wall-clock time was ≤ 45 minutes (target: 30 minutes).

The smoke test fails — file a bug — if any of the following:
- A KB write was lost (a successful tool call did not produce expected rows).
- The LLM tried to invoke a tool name that does not exist (e.g. `netexec_dump_secrets`).
- A scope violation was triggered without a `scope.toml` being present.
- BloodHound's bolt-port collision triggered without a clear remediation message.
- The LLM cracked hashes inside the agent (it must surface for offline cracking).

---

## Re-run hygiene

To re-run the smoke test cleanly against the same box:

```sh
# Stop Neo4j if still running
reverser i -p ad <BOX_IP>  # then F1 → … or just bash:
ps aux | grep neo4j

# Wipe the KB and per-target dir
rm -rf targets/<BOX_IP>
```

Do NOT delete `targets/` itself — other targets live there.
```

- [ ] **Step 2: Final length check**

Run: `wc -l tests/manual/ad_smoke.md`
Expected: ~280–320 lines total.

- [ ] **Step 3: Commit**

```bash
git add tests/manual/ad_smoke.md
git commit -m "test(manual): add AD smoke-test steps 11-13 + closeout"
```

---

## Task 14: Final integration validation — full test suite + profile listing

**Files:**
- Read-only verification

- [ ] **Step 1: Run the full Python test suite**

Run: `pytest -v`

Expected: All tests from Plans 1–5 pass. Specifically:
- Plan 1: `test_kb_authz.py`, `test_kb_schema.py`, `test_kb_store.py`, `test_kb_integration.py`
- Plan 2: `test_kb_tools.py`, `test_parsers_*.py`
- Plan 3: `test_netexec*.py`
- Plan 4: `test_bloodhound*.py`
- Plan 5: `test_kb_scope.py`, `test_profiles_ad.py`

Note the total test count.

- [ ] **Step 2: Verify all profiles still load**

Run:
```sh
python -c "
from reverser.profiles import PROFILES, list_profiles, get_profile
profiles = list_profiles()
print(f'Total profiles: {len(profiles)}')
for p in profiles:
    print(f'  {p.key:12s} ({len(p.skills):2d} skills) - {p.name}')
print()
print(f'AD profile exists: {\"ad\" in PROFILES}')
print(f'AD profile name: {get_profile(\"ad\").name}')
print(f'AD skills: {[s.name for s in get_profile(\"ad\").skills]}')
"
```

Expected:
- 13 total profiles.
- The `ad` profile lists all 11 expected skill names.
- The `pentest` profile system_addendum mentions "ad" and "-p ad".

- [ ] **Step 3: Verify the CLI surface still works**

Run: `reverser interactive --list-profiles`
Expected: Output includes `ad` (Active Directory) with its 11 skills listed.

If the CLI uses a slightly different flag (`--profiles`, etc.), grep `src/reverser/cli.py` for the listing entrypoint and run the right command.

- [ ] **Step 4: Verify the AD prompt renders cleanly under the harness**

Run a quick end-to-end render of the prompt — start the TUI in `--dry-run` mode if available, or simply launch `reverser i -p ad 10.10.10.5` (against an unreachable IP), confirm the system prompt panel renders the AD addendum without truncation or syntax errors, then quit.

- [ ] **Step 5: Verify scope.toml enforcement is wired (smoke check)**

Run:
```sh
mkdir -p /tmp/reverser-scope-test/targets/172.16.0.1
cat > /tmp/reverser-scope-test/targets/172.16.0.1/scope.toml <<EOF
[scope]
in_scope_cidrs = ["10.10.10.0/24"]
EOF
REVERSER_TARGETS_DIR=/tmp/reverser-scope-test/targets python -c "
from reverser.kb.scope import load_scope
s = load_scope('172.16.0.1')
print('loaded:', s)
print('in_scope(10.10.10.42):', s.is_target_in_scope('10.10.10.42'))
print('in_scope(172.16.0.99):', s.is_target_in_scope('172.16.0.99'))
"
rm -rf /tmp/reverser-scope-test
```

Expected:
```
loaded: Scope(in_scope_cidrs=['10.10.10.0/24'], out_of_scope_ips=[], allowed_hours=None, no_dos=False, no_account_lockout=False)
in_scope(10.10.10.42): True
in_scope(172.16.0.99): False
```

- [ ] **Step 6: Final commit (only if any cleanup was needed)**

If everything passed without changes, skip this commit. Otherwise:

```bash
git commit -am "chore: integration validation cleanup for plan 5"
```

---

## Task 15: Mark the AD capability complete

**Files:**
- Modify: `CAPABILITY_ROADMAP.md` (if present) — mark items #1 and #4 done.

- [ ] **Step 1: Check whether `CAPABILITY_ROADMAP.md` exists**

Run: `test -f CAPABILITY_ROADMAP.md && echo present || echo absent`

If absent, skip this task entirely (no commit).

- [ ] **Step 2: Update the roadmap entries for items #1 and #4**

If the file exists, locate the entries for "NetExec / BloodHound / Cypher" (item #1) and
"Per-target KB" (item #4). Append a status line to each:

```markdown
> **Status (2026-05-03):** Shipped via Plans 1–5. See
> `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` and
> `docs/superpowers/plans/2026-05-03-plan-{1..5}-*.md`.
```

- [ ] **Step 3: Commit**

```bash
git add CAPABILITY_ROADMAP.md
git commit -m "docs(roadmap): mark NetExec/BloodHound and per-target KB shipped"
```

---

## Done

Plan 5 closes the AD capability pack:
- The `ad` profile is registered with a hypothesis-driven, KB-aware system prompt and 11 task-specific skills covering the full assumed-breach methodology.
- The existing `pentest` profile now recognizes AD environments and recommends the `-p ad` pivot.
- An optional `targets/<target>/scope.toml` provides a tight enforcement envelope; absent the file, behavior is unchanged from earlier plans.
- `devenv.nix` provisions `neo4j`, `netexec`, `bloodhound-python`, and the `neo4j` Python driver.
- The `tests/manual/ad_smoke.md` checklist provides a 30-minute proof-of-life walkthrough against an HTB AD lab box, with explicit KB-state verification at each step.

This is the final plan in the AD capability series. Subsequent roadmap work — searchsploit/msfvenom integration (item #2), Playwright wiring (item #3), hypothesis-loop prompt restructure (item #5) — is tracked separately and out of scope here.
