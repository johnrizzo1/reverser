# Plan 3 — NetExec Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/reverser/tools/netexec.py` containing 6 NetExec wrapper tools (one per protocol — SMB, WinRM, LDAP, MSSQL, SSH, FTP/WMI), each integrated with the per-target KB for the credential lifecycle (untested → invalid → valid), host/service recording, and dump-artifact capture. Tools enforce a hard authorization gate, transparent credential fallback from the KB, and built-in spray guardrails (`REVERSER_AD_ALLOW_SPRAY`, `REVERSER_SPRAY_MAX`).

**Architecture:** A single module `src/reverser/tools/netexec.py` exposes 6 `@tool`-decorated async functions. A small set of private helpers (`_resolve_credential`, `_check_spray_allowed`, `_save_dump_artifact`, `_parse_nxc_status_line`, `_parse_nxc_share_table`, `_parse_nxc_ldap_computers`, `_parse_nxc_secret_dump`) live at the top of the file. All tools call `require_pentest_auth()` first, then `for_target(target)` to obtain the KB instance. Subprocess wrapping reuses `run_cmd`/`format_tool_result`/`format_error`/`cmd_result_to_tool_result` from `tools/_common.py`. NetExec is invoked via the `nxc` binary (formerly `crackmapexec`/`cme`), one process per call.

**Tech Stack:** Python 3.11+, NetExec (`nxc` CLI). Depends on Plan 1 (`reverser.kb`).

**Spec reference:** `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` § NetExec tools.

---

## File Structure

**Created:**
- `src/reverser/tools/netexec.py` — 6 NetExec tools + shared helpers
- `tests/test_netexec_helpers.py` — unit tests for shared helpers (credential fallback, spray guardrail, parsers, dump-saver)
- `tests/test_netexec_smb.py` — netexec_smb tool tests
- `tests/test_netexec_winrm.py` — netexec_winrm tool tests
- `tests/test_netexec_ldap.py` — netexec_ldap tool tests
- `tests/test_netexec_mssql.py` — netexec_mssql tool tests
- `tests/test_netexec_ssh.py` — netexec_ssh tool tests
- `tests/test_netexec_ftp_wmi.py` — netexec_ftp_wmi tool tests
- `tests/test_netexec_integration.py` — end-to-end smoke (fully mocked subprocess, exercises all 6 tools)

**Modified:**
- `src/reverser/tools/__init__.py` — import + register `netexec_tools`
- `devenv.nix` — add `netexec` to `pkgs` list

---

## Task 1: Add netexec to devenv.nix

**Files:**
- Modify: `devenv.nix`

- [ ] **Step 1: Read current devenv.nix to find the pkgs list**

Run: `cat devenv.nix | grep -n "pkgs\." | head -30`

Locate the `packages = [` block (or `packages = with pkgs; [`).

- [ ] **Step 2: Add `netexec` to the package list**

Edit `devenv.nix`. Inside the `packages = with pkgs; [ ... ]` block (or equivalent), add a new line near the security/network tooling entries:

```nix
    netexec               # nxc — successor to crackmapexec; AD enumeration/exploitation
```

If the block uses fully-qualified names (e.g. `pkgs.netexec`), match the existing style.

- [ ] **Step 3: Reload devenv and verify nxc is on PATH**

Run: `devenv shell -- which nxc`
Expected: a path under `/nix/store/.../bin/nxc`.

If `nxc` is not yet packaged in the user's nixpkgs channel, document that the user will need to install via `pip install netexec` inside the venv as a fallback. Add a comment line in `devenv.nix` near the entry:

```nix
    # netexec — if nixpkgs lacks this attribute, fall back to: pip install netexec
```

- [ ] **Step 4: Verify nxc runs and reports its version**

Run: `nxc --version`
Expected: a version string (e.g. `1.x.x` or similar). Non-zero exit = nxc not actually installed; investigate before proceeding.

- [ ] **Step 5: Commit**

```bash
git add devenv.nix
git commit -m "chore(devenv): add netexec (nxc) for AD enumeration"
```

---

## Task 2: Shared helpers — credential fallback + spray guardrail + dump-saver

**Files:**
- Create: `src/reverser/tools/netexec.py` (initial — module skeleton + helpers)
- Create: `tests/test_netexec_helpers.py`

- [ ] **Step 1: Create the module skeleton with shared helpers**

`src/reverser/tools/netexec.py`:

```python
"""NetExec (nxc) wrapper tools — one per protocol — with KB integration.

All tools:
- require pentest authorization at function entry
- fall back to KB-stored valid credentials if username/password/nt_hash omitted
- enforce spray guardrails via REVERSER_AD_ALLOW_SPRAY + REVERSER_SPRAY_MAX
- write all observed facts (creds, hosts, dumps, shares) into the per-target KB
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from claude_agent_sdk import tool

from ._common import (
    cmd_result_to_tool_result,
    format_error,
    format_tool_result,
    run_cmd,
)

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────

DEFAULT_SPRAY_MAX = 3
NXC_TIMEOUT_FAST = 60       # check_auth, single-host enumeration
NXC_TIMEOUT_MEDIUM = 180    # share spider, computer enum
NXC_TIMEOUT_SLOW = 600      # ntds dump, lsa dump, sam dump, large spray


# ── Credential fallback ─────────────────────────────────────────────

@dataclass
class ResolvedCredential:
    """A credential to use for the NetExec call, plus a one-line origin string."""
    username: Optional[str]
    password: Optional[str]
    nt_hash: Optional[str]
    domain: Optional[str]
    origin: str   # e.g. "explicit args" or "[KB] Using credential: jdoe (validated via netexec_smb)"


def _resolve_credential(
    target: str,
    username: Optional[str],
    password: Optional[str],
    nt_hash: Optional[str],
    domain: Optional[str],
) -> tuple[Optional[ResolvedCredential], Optional[str]]:
    """Resolve credentials, falling back to KB if all auth args are empty.

    Returns (cred, error). If error is non-None, cred is None and the caller
    should return format_error(error). If both are None, the call is
    intentionally unauthenticated (anonymous / null session) — caller decides
    if that is acceptable for the requested action.
    """
    if username or password or nt_hash:
        return ResolvedCredential(
            username=username or None,
            password=password or None,
            nt_hash=nt_hash or None,
            domain=domain or None,
            origin="explicit args",
        ), None

    # All auth args empty — try KB fallback
    try:
        from ..kb import for_target
        kb = for_target(target)
        valid = kb.get_credentials(status="valid")
    except Exception as e:
        logger.warning("KB credential fallback failed: %s", e)
        return None, (
            "No credentials supplied and KB lookup failed. "
            f"Provide username + password (or nt_hash). KB error: {e}"
        )

    if not valid:
        return None, (
            "No credentials supplied and no valid credentials in KB for this target. "
            "Either pass username + password / nt_hash explicitly, or run a working "
            "check_auth first to populate the KB."
        )

    # Pick the most-recently-recorded valid credential (last in list — KB orders by id ASC).
    chosen = valid[-1]
    origin = (
        f"[KB] Using credential: {chosen.username}"
        + (f"@{chosen.domain}" if chosen.domain else "")
        + (f" (source={chosen.source_tool})" if chosen.source_tool else "")
    )
    return ResolvedCredential(
        username=chosen.username,
        password=chosen.password,
        nt_hash=chosen.nt_hash,
        domain=chosen.domain or domain or None,
        origin=origin,
    ), None


# ── Spray guardrail ─────────────────────────────────────────────────

def _check_spray_allowed() -> Optional[str]:
    """Return an error string if spray is not allowed; None if it is."""
    if os.environ.get("REVERSER_AD_ALLOW_SPRAY") != "1":
        return (
            "Spray actions are disabled. Set REVERSER_AD_ALLOW_SPRAY=1 to enable. "
            "Spray can lock out accounts; only enable after confirming the engagement "
            "rules-of-engagement permit it. The hard cap REVERSER_SPRAY_MAX (default "
            f"{DEFAULT_SPRAY_MAX}) limits attempts per user even when enabled."
        )
    return None


def _spray_max() -> int:
    """Return the per-user attempt cap from env, with a sane default."""
    raw = os.environ.get("REVERSER_SPRAY_MAX", str(DEFAULT_SPRAY_MAX))
    try:
        n = int(raw)
        if n < 1:
            return DEFAULT_SPRAY_MAX
        return n
    except ValueError:
        return DEFAULT_SPRAY_MAX


# ── Dump artifact saver ─────────────────────────────────────────────

def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _save_dump_artifact(target: str, kind: str, content: str) -> tuple[Path, str]:
    """Save a dump (sam/lsa/ntds output) to targets/<target>/loot/.

    Returns (path, sha256). Caller still needs to call kb.record_artifact.
    """
    from ..kb import for_target
    kb = for_target(target)
    loot_dir = kb.root / "loot"
    loot_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{kind}_{_timestamp()}.txt"
    path = loot_dir / fname
    path.write_text(content, encoding="utf-8", errors="replace")
    return path, _sha256_of_text(content)


# ── NetExec output parsers ──────────────────────────────────────────

# NetExec status lines look like:
#   SMB         10.10.10.5      445    DC01             [+] CORP.LOCAL\jdoe:Summer2026! (Pwn3d!)
#   SMB         10.10.10.5      445    DC01             [-] CORP.LOCAL\jdoe:bad STATUS_LOGON_FAILURE
#   LDAP        10.10.10.5      389    DC01             [+] CORP.LOCAL\jdoe:Summer2026!
_NXC_STATUS_RE = re.compile(
    r"^\s*(?P<proto>\S+)\s+(?P<ip>\S+)\s+(?P<port>\d+)\s+(?P<host>\S+)\s+"
    r"\[(?P<sign>[+\-*!])\]\s*(?P<rest>.*)$"
)


def _parse_nxc_status_line(line: str) -> Optional[dict]:
    """Parse a single NetExec status line. Returns dict or None.

    Returned dict keys: proto, ip, port, host, sign ('+'/'-'/'*'/'!'), rest.
    """
    m = _NXC_STATUS_RE.match(line)
    if not m:
        return None
    return {
        "proto": m.group("proto"),
        "ip": m.group("ip"),
        "port": int(m.group("port")),
        "host": m.group("host"),
        "sign": m.group("sign"),
        "rest": m.group("rest").strip(),
    }


def _auth_succeeded(stdout: str) -> bool:
    """Return True if any NetExec line indicates a successful authentication."""
    for line in stdout.splitlines():
        parsed = _parse_nxc_status_line(line)
        if parsed and parsed["sign"] == "+":
            return True
    return False


# Share table looks like:
#   SMB    10.10.10.5    445   DC01   Share           Permissions     Remark
#   SMB    10.10.10.5    445   DC01   -----           -----------     ------
#   SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin
#   SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC
_SHARE_ROW_RE = re.compile(
    r"^\s*SMB\s+\S+\s+\d+\s+\S+\s+(?P<share>\S+)\s+(?P<perms>[A-Z,]*)\s*(?P<remark>.*)$"
)


def _parse_nxc_share_table(stdout: str) -> list[dict]:
    """Parse the share-listing output. Returns list of {share, perms, remark}."""
    out: list[dict] = []
    for line in stdout.splitlines():
        m = _SHARE_ROW_RE.match(line)
        if not m:
            continue
        share = m.group("share")
        if share in ("Share", "-----"):
            continue
        out.append({
            "share": share,
            "perms": m.group("perms") or "",
            "remark": m.group("remark").strip(),
        })
    return out


# LDAP/computers output looks like:
#   LDAP    10.10.10.5   389   DC01   DC01.CORP.LOCAL
#   LDAP    10.10.10.5   389   DC01   WS01.CORP.LOCAL
_LDAP_COMPUTER_RE = re.compile(
    r"^\s*LDAP\s+(?P<ip>\S+)\s+\d+\s+\S+\s+(?P<fqdn>[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)\s*$"
)


def _parse_nxc_ldap_computers(stdout: str) -> list[dict]:
    """Parse LDAP --computers output. Returns list of {ip, fqdn, hostname, domain}."""
    out: list[dict] = []
    for line in stdout.splitlines():
        m = _LDAP_COMPUTER_RE.match(line)
        if not m:
            continue
        fqdn = m.group("fqdn")
        host, _, dom = fqdn.partition(".")
        out.append({
            "ip": m.group("ip"),
            "fqdn": fqdn,
            "hostname": host,
            "domain": dom or None,
        })
    return out


# Secret dump lines (sam/lsa/ntds) look like:
#   Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::
#   krbtgt:502:aad3b...:31d6cfe0d16ae931b73c59d7e0c089c0:::
_PWDUMP_RE = re.compile(
    r"^(?P<user>[^:\s][^:]*):(?P<rid>\d+):(?P<lm>[a-fA-F0-9]{32}):(?P<nt>[a-fA-F0-9]{32}):"
)


def _parse_nxc_secret_dump(stdout: str) -> list[dict]:
    """Parse pwdump-format hashes from sam/lsa/ntds output.

    Returns list of {username, rid, lm_hash, nt_hash}.
    """
    out: list[dict] = []
    for line in stdout.splitlines():
        m = _PWDUMP_RE.match(line.strip())
        if not m:
            continue
        out.append({
            "username": m.group("user"),
            "rid": int(m.group("rid")),
            "lm_hash": m.group("lm"),
            "nt_hash": m.group("nt"),
        })
    return out


# ── Common cmd-builder ──────────────────────────────────────────────

def _build_auth_args(cred: ResolvedCredential, local_auth: bool = False) -> list[str]:
    """Translate a ResolvedCredential into nxc CLI flags."""
    args: list[str] = []
    if cred.username:
        args.extend(["-u", cred.username])
    if cred.password is not None:
        args.extend(["-p", cred.password])
    if cred.nt_hash:
        args.extend(["-H", cred.nt_hash])
    if cred.domain:
        args.extend(["-d", cred.domain])
    if local_auth:
        args.append("--local-auth")
    return args


# ── Tool implementations follow in subsequent tasks ─────────────────


TOOLS: list = []  # populated at module bottom after each tool is defined
```

- [ ] **Step 2: Write failing tests for the helpers**

`tests/test_netexec_helpers.py`:

```python
"""Unit tests for the netexec module's shared helpers."""

import os
import pytest

from reverser.tools.netexec import (
    DEFAULT_SPRAY_MAX,
    ResolvedCredential,
    _auth_succeeded,
    _build_auth_args,
    _check_spray_allowed,
    _parse_nxc_ldap_computers,
    _parse_nxc_secret_dump,
    _parse_nxc_share_table,
    _parse_nxc_status_line,
    _resolve_credential,
    _save_dump_artifact,
    _spray_max,
)


# ── _resolve_credential ─────────────────────────────────────────────

def test_resolve_credential_explicit_password(tmp_targets_dir):
    cred, err = _resolve_credential("10.10.10.5", "jdoe", "pw", None, "CORP")
    assert err is None
    assert cred.username == "jdoe"
    assert cred.password == "pw"
    assert cred.domain == "CORP"
    assert cred.origin == "explicit args"


def test_resolve_credential_explicit_hash(tmp_targets_dir):
    cred, err = _resolve_credential("10.10.10.5", "jdoe", None, "aad3b...", None)
    assert err is None
    assert cred.nt_hash == "aad3b..."
    assert cred.password is None


def test_resolve_credential_no_creds_no_kb(tmp_targets_dir):
    """All auth args empty + empty KB → returns error."""
    cred, err = _resolve_credential("10.10.10.5", None, None, None, None)
    assert cred is None
    assert "No credentials supplied" in err
    assert "no valid credentials in KB" in err


def test_resolve_credential_falls_back_to_kb(tmp_targets_dir):
    """All auth args empty + valid KB cred → uses KB cred and reports it."""
    from reverser.kb import for_target, CredentialFact
    kb = for_target("10.10.10.5")
    kb.record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    cred, err = _resolve_credential("10.10.10.5", None, None, None, None)
    assert err is None
    assert cred.username == "jdoe"
    assert cred.password == "Summer2026!"
    assert cred.domain == "CORP"
    assert "[KB] Using credential: jdoe" in cred.origin


def test_resolve_credential_picks_most_recent_valid(tmp_targets_dir):
    """When multiple valid creds exist, the most-recently-inserted wins."""
    from reverser.kb import for_target, CredentialFact
    kb = for_target("10.10.10.5")
    kb.record_credential(CredentialFact(username="alice", password="a", status="valid"))
    kb.record_credential(CredentialFact(username="bob", password="b", status="valid"))
    cred, err = _resolve_credential("10.10.10.5", None, None, None, None)
    assert err is None
    assert cred.username == "bob"


# ── _check_spray_allowed / _spray_max ───────────────────────────────

def test_spray_blocked_by_default(monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    err = _check_spray_allowed()
    assert err is not None
    assert "REVERSER_AD_ALLOW_SPRAY" in err
    assert "REVERSER_SPRAY_MAX" in err


def test_spray_allowed_with_env(monkeypatch):
    monkeypatch.setenv("REVERSER_AD_ALLOW_SPRAY", "1")
    assert _check_spray_allowed() is None


def test_spray_max_default(monkeypatch):
    monkeypatch.delenv("REVERSER_SPRAY_MAX", raising=False)
    assert _spray_max() == DEFAULT_SPRAY_MAX


def test_spray_max_override(monkeypatch):
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "5")
    assert _spray_max() == 5


def test_spray_max_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "abc")
    assert _spray_max() == DEFAULT_SPRAY_MAX


def test_spray_max_negative_falls_back(monkeypatch):
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "-1")
    assert _spray_max() == DEFAULT_SPRAY_MAX


# ── _save_dump_artifact ─────────────────────────────────────────────

def test_save_dump_artifact_creates_file(tmp_targets_dir):
    path, sha = _save_dump_artifact("10.10.10.5", "ntds_dump", "Administrator:500:aad3...:8846...:::\n")
    assert path.exists()
    assert path.read_text().startswith("Administrator:500:")
    assert path.parent.name == "loot"
    assert path.name.startswith("ntds_dump_")
    assert path.name.endswith(".txt")
    assert len(sha) == 64


# ── parsers ─────────────────────────────────────────────────────────

def test_parse_nxc_status_line_success():
    line = "SMB         10.10.10.5      445    DC01             [+] CORP.LOCAL\\jdoe:Summer2026! (Pwn3d!)"
    parsed = _parse_nxc_status_line(line)
    assert parsed is not None
    assert parsed["proto"] == "SMB"
    assert parsed["ip"] == "10.10.10.5"
    assert parsed["port"] == 445
    assert parsed["host"] == "DC01"
    assert parsed["sign"] == "+"
    assert "Pwn3d" in parsed["rest"]


def test_parse_nxc_status_line_failure():
    line = "SMB    10.10.10.5    445    DC01    [-] CORP.LOCAL\\jdoe:bad STATUS_LOGON_FAILURE"
    parsed = _parse_nxc_status_line(line)
    assert parsed is not None
    assert parsed["sign"] == "-"


def test_parse_nxc_status_line_unparseable():
    assert _parse_nxc_status_line("garbage line") is None
    assert _parse_nxc_status_line("") is None


def test_auth_succeeded_true():
    out = (
        "SMB    10.10.10.5    445    DC01    [*] Windows Server 2019\n"
        "SMB    10.10.10.5    445    DC01    [+] CORP.LOCAL\\jdoe:Summer2026!\n"
    )
    assert _auth_succeeded(out) is True


def test_auth_succeeded_false():
    out = (
        "SMB    10.10.10.5    445    DC01    [*] Windows Server 2019\n"
        "SMB    10.10.10.5    445    DC01    [-] CORP.LOCAL\\jdoe:bad\n"
    )
    assert _auth_succeeded(out) is False


def test_parse_nxc_share_table():
    out = (
        "SMB    10.10.10.5    445   DC01   [*] Enumerated shares\n"
        "SMB    10.10.10.5    445   DC01   Share           Permissions     Remark\n"
        "SMB    10.10.10.5    445   DC01   -----           -----------     ------\n"
        "SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin\n"
        "SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC\n"
        "SMB    10.10.10.5    445   DC01   NETLOGON        READ            Logon server share\n"
    )
    rows = _parse_nxc_share_table(out)
    names = [r["share"] for r in rows]
    assert "ADMIN$" in names
    assert "IPC$" in names
    assert "NETLOGON" in names
    admin = [r for r in rows if r["share"] == "ADMIN$"][0]
    assert "READ" in admin["perms"]
    assert "WRITE" in admin["perms"]


def test_parse_nxc_ldap_computers():
    out = (
        "LDAP    10.10.10.5   389   DC01   DC01.CORP.LOCAL\n"
        "LDAP    10.10.10.5   389   DC01   WS01.CORP.LOCAL\n"
        "LDAP    10.10.10.5   389   DC01   [*] noise line\n"
    )
    rows = _parse_nxc_ldap_computers(out)
    fqdns = [r["fqdn"] for r in rows]
    assert "DC01.CORP.LOCAL" in fqdns
    assert "WS01.CORP.LOCAL" in fqdns
    assert all(r["domain"] == "CORP.LOCAL" for r in rows)


def test_parse_nxc_secret_dump():
    out = (
        "[+] Dumping NTDS\n"
        "Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::\n"
        "krbtgt:502:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::\n"
        "garbage\n"
    )
    rows = _parse_nxc_secret_dump(out)
    assert len(rows) == 2
    users = [r["username"] for r in rows]
    assert "Administrator" in users
    assert "krbtgt" in users
    admin = [r for r in rows if r["username"] == "Administrator"][0]
    assert admin["rid"] == 500
    assert admin["nt_hash"] == "8846f7eaee8fb117ad06bdd830b7586c"


# ── _build_auth_args ────────────────────────────────────────────────

def test_build_auth_args_password():
    cred = ResolvedCredential(username="jdoe", password="pw", nt_hash=None, domain="CORP", origin="x")
    args = _build_auth_args(cred)
    assert args == ["-u", "jdoe", "-p", "pw", "-d", "CORP"]


def test_build_auth_args_hash_local():
    cred = ResolvedCredential(username="admin", password=None, nt_hash="aad3b...", domain=None, origin="x")
    args = _build_auth_args(cred, local_auth=True)
    assert args == ["-u", "admin", "-H", "aad3b...", "--local-auth"]


def test_build_auth_args_empty_password():
    """Password can be the empty string (intentional anon-bind-with-username)."""
    cred = ResolvedCredential(username="guest", password="", nt_hash=None, domain=None, origin="x")
    args = _build_auth_args(cred)
    assert args == ["-u", "guest", "-p", ""]
```

- [ ] **Step 3: Run the tests — expect failures (helpers exist, but test imports may not align until the module is on the path)**

Run: `pytest tests/test_netexec_helpers.py -v`
Expected: All 21 tests pass on first run, since the helpers were defined in Step 1. If any fail, fix the helper to match the tested contract before proceeding.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_helpers.py
git commit -m "feat(netexec): module skeleton with cred fallback, spray guardrail, parsers"
```

---

## Task 3: netexec_smb tool

**Files:**
- Modify: `src/reverser/tools/netexec.py` (append `netexec_smb`)
- Create: `tests/test_netexec_smb.py`

- [ ] **Step 1: Write failing tests**

`tests/test_netexec_smb.py`:

```python
"""Tests for netexec_smb."""

import os
from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_smb


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_smb_unauthorized_raises(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        await netexec_smb({"target": "10.10.10.5", "action": "check_auth",
                           "username": "jdoe", "password": "x"})


@pytest.mark.asyncio
async def test_smb_check_auth_success_records_valid_cred(tmp_targets_dir):
    out = "SMB    10.10.10.5    445   DC01   [+] CORP.LOCAL\\jdoe:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        result = await netexec_smb({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })
    assert "is_error" not in result or not result["is_error"]
    text = result["content"][0]["text"]
    assert "Pwn3d" in text or "+" in text

    from reverser.kb import for_target
    creds = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in creds)


@pytest.mark.asyncio
async def test_smb_check_auth_failure_records_invalid_cred(tmp_targets_dir):
    out = "SMB    10.10.10.5    445   DC01   [-] CORP\\jdoe:bad STATUS_LOGON_FAILURE\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_smb({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "bad", "domain": "CORP",
        })
    from reverser.kb import for_target
    invalid = for_target("10.10.10.5").get_credentials(status="invalid")
    assert any(c.username == "jdoe" for c in invalid)


@pytest.mark.asyncio
async def test_smb_no_creds_uses_kb_fallback(tmp_targets_dir):
    """No creds in args + valid cred in KB → uses the KB cred."""
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", status="valid",
    ))

    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("SMB    10.10.10.5    445   DC01   [+] jdoe:Summer2026!\n")

    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        result = await netexec_smb({"target": "10.10.10.5", "action": "check_auth"})

    assert "-u" in captured["cmd"] and "jdoe" in captured["cmd"]
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_smb_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = await netexec_smb({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True
    assert "no valid credentials" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_smb_shares_records_note(tmp_targets_dir):
    out = (
        "SMB    10.10.10.5    445   DC01   Share           Permissions     Remark\n"
        "SMB    10.10.10.5    445   DC01   -----           -----------     ------\n"
        "SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin\n"
        "SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_smb({
            "target": "10.10.10.5", "action": "shares",
            "username": "jdoe", "password": "x",
        })
    from reverser.kb import for_target
    notes = for_target("10.10.10.5").get_notes()
    assert any("ADMIN$" in n for n in notes)


@pytest.mark.asyncio
async def test_smb_ntds_dump_saves_artifact_and_creds(tmp_targets_dir):
    out = (
        "[+] Dumping NTDS\n"
        "Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::\n"
        "krbtgt:502:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_smb({
            "target": "10.10.10.5", "action": "ntds",
            "username": "admin", "password": "x", "local_auth": True,
        })
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    arts = kb.get_artifacts()
    assert any(a.kind == "ntds_dump" for a in arts)
    assert any(a.source_tool == "netexec_smb" for a in arts)
    untested = kb.get_credentials(status="untested")
    usernames = [c.username for c in untested]
    assert "Administrator" in usernames
    assert "krbtgt" in usernames


@pytest.mark.asyncio
async def test_smb_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = await netexec_smb({
        "target": "10.10.10.5", "action": "spray",
        "username": "jdoe", "password": "Summer2026!",
    })
    assert result.get("is_error") is True
    assert "REVERSER_AD_ALLOW_SPRAY" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_smb_spray_caps_attempts(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_AD_ALLOW_SPRAY", "1")
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "2")
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_smb({
            "target": "10.10.10.5", "action": "spray",
            "username": "jdoe", "password": "Summer2026!",
        })
    cmd_str = " ".join(captured["cmd"])
    # Hard cap surfaces somewhere — either via --max-failed-logins or by us
    # batching at the wrapper level. Either way, the env's value (2) must be
    # reflected in the constructed command.
    assert "2" in cmd_str
    # Continue-on-success must NOT be on by default
    assert "--continue-on-success" not in cmd_str


@pytest.mark.asyncio
async def test_smb_module_invocation(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("[*] module ran")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_smb({
            "target": "10.10.10.5", "action": "exec",
            "username": "jdoe", "password": "x",
            "module": "lsassy",
        })
    cmd = captured["cmd"]
    assert "-M" in cmd and "lsassy" in cmd
```

- [ ] **Step 2: Run to verify the tests fail with ImportError (tool not yet defined)**

Run: `pytest tests/test_netexec_smb.py -v`
Expected: `ImportError: cannot import name 'netexec_smb'`.

- [ ] **Step 3: Implement `netexec_smb` — append to `src/reverser/tools/netexec.py`**

Append at the bottom (above the `TOOLS = []` line), then update the `TOOLS = [...]` line:

```python
# ── netexec_smb ─────────────────────────────────────────────────────

_SMB_ACTIONS = {
    "shares", "users", "groups", "computers", "pass_pol", "rid_brute",
    "sam", "lsa", "ntds", "loggedon", "sessions", "disks", "spider",
    "exec", "spray", "check_auth",
}

_SMB_ACTION_TO_FLAG = {
    "shares": ["--shares"],
    "users": ["--users"],
    "groups": ["--groups"],
    "computers": ["--computers"],
    "pass_pol": ["--pass-pol"],
    "rid_brute": ["--rid-brute"],
    "sam": ["--sam"],
    "lsa": ["--lsa"],
    "ntds": ["--ntds"],
    "loggedon": ["--loggedon-users"],
    "sessions": ["--sessions"],
    "disks": ["--disks"],
    "spider": ["--spider", "C$"],
}

_SMB_DUMP_KIND = {"sam": "sam_hashes", "lsa": "lsa_secrets", "ntds": "ntds_dump"}


@tool(
    "netexec_smb",
    "NetExec SMB protocol wrapper. Enumerate shares/users/groups/computers, dump "
    "SAM/LSA/NTDS, run commands, and validate credentials. Falls back to KB-stored "
    "valid credentials if no auth args are given. Spray actions require "
    "REVERSER_AD_ALLOW_SPRAY=1 and are capped at REVERSER_SPRAY_MAX attempts/user.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target IP, hostname, or CIDR"},
            "action": {
                "type": "string",
                "enum": sorted(_SMB_ACTIONS),
                "description": "What to do over SMB",
            },
            "username": {"type": "string", "default": ""},
            "password": {"type": "string", "default": ""},
            "nt_hash": {"type": "string", "default": ""},
            "domain": {"type": "string", "default": ""},
            "local_auth": {"type": "boolean", "default": False,
                           "description": "Treat the credential as local (not domain)"},
            "module": {"type": "string", "default": "",
                       "description": "Optional NetExec module name (lsassy, spider_plus, coerce_plus, ...)"},
            "command": {"type": "string", "default": "",
                        "description": "Command to run (only for action=exec)"},
            "extra_args": {"type": "string", "default": ""},
        },
        "required": ["target", "action"],
    },
)
async def netexec_smb(args: dict) -> dict:
    from ..kb import (
        for_target, require_pentest_auth,
        CredentialFact, CredResult, ArtifactFact,
    )
    require_pentest_auth()

    target = args["target"]
    action = args["action"]
    if action not in _SMB_ACTIONS:
        return format_error(f"Unknown SMB action: {action}. Valid: {sorted(_SMB_ACTIONS)}")

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    local_auth = bool(args.get("local_auth", False))
    module = (args.get("module", "") or "").strip()
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    # Spray guardrail — applied BEFORE any credential resolution so a missing
    # env var fails fast with a useful error.
    if action == "spray":
        spray_err = _check_spray_allowed()
        if spray_err:
            return format_error(spray_err)

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)

    cmd: list[str] = ["nxc", "smb", target]
    cmd.extend(_build_auth_args(cred, local_auth=local_auth))

    if action in _SMB_ACTION_TO_FLAG:
        cmd.extend(_SMB_ACTION_TO_FLAG[action])
    elif action == "exec":
        if not command:
            return format_error("action=exec requires command argument")
        cmd.extend(["-x", command])
    elif action == "spray":
        # Spray uses the same -p/-H but adds the cap. NetExec exposes
        # --max-failed-logins; use it AND also withhold --continue-on-success.
        cmd.extend(["--max-failed-logins", str(_spray_max())])
    elif action == "check_auth":
        pass  # plain auth check is what nxc smb does by default

    if module:
        cmd.extend(["-M", module])

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    timeout = NXC_TIMEOUT_SLOW if action in ("ntds", "lsa", "sam", "spider", "spray") else NXC_TIMEOUT_FAST
    result = run_cmd(cmd, timeout=timeout, max_output=32000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)

    # Credential lifecycle (only meaningful for actions that actually authed)
    cred_id: Optional[int] = None
    if cred.username and (cred.password is not None or cred.nt_hash):
        try:
            status = "valid" if success else "invalid"
            cred_id = kb.record_credential(CredentialFact(
                username=cred.username, password=cred.password, nt_hash=cred.nt_hash,
                domain=cred.domain, source_tool="netexec_smb", status=status,
            ))
            kb.record_cred_result(cred_id, CredResult(
                service_kind="smb", target_host=target, success=success,
                error_msg=None if success else (result.get("stderr") or "auth failed")[:500],
            ))
        except Exception as e:
            logger.warning("KB cred-write failed in netexec_smb: %s", e)

    # Action-specific KB writes
    try:
        if action == "shares" and stdout:
            shares = _parse_nxc_share_table(stdout)
            if shares:
                body = "SMB shares on {}:\n".format(target) + "\n".join(
                    f"  {s['share']:20s}  {s['perms']:15s}  {s['remark']}" for s in shares
                )
                kb.record_note(body)
        elif action in _SMB_DUMP_KIND and stdout:
            kind = _SMB_DUMP_KIND[action]
            path, sha = _save_dump_artifact(target, kind, stdout)
            kb.record_artifact(ArtifactFact(
                kind=kind, path=str(path), sha256=sha, source_tool="netexec_smb",
            ))
            for hdump in _parse_nxc_secret_dump(stdout):
                try:
                    kb.record_credential(CredentialFact(
                        username=hdump["username"], nt_hash=hdump["nt_hash"],
                        lm_hash=hdump["lm_hash"], domain=cred.domain,
                        source_tool="netexec_smb",
                        source_context=f"{action} dump from {target}",
                        status="untested",
                    ))
                except Exception as e:
                    logger.warning("KB hash record failed: %s", e)
    except Exception as e:
        logger.warning("KB action-write failed in netexec_smb: %s", e)

    # Display result with origin prefix
    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc smb failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_smb)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_netexec_smb.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_smb.py
git commit -m "feat(netexec): netexec_smb with cred lifecycle, dump capture, spray cap"
```

---

## Task 4: netexec_winrm tool

**Files:**
- Modify: `src/reverser/tools/netexec.py` (append `netexec_winrm`)
- Create: `tests/test_netexec_winrm.py`

- [ ] **Step 1: Write failing tests**

`tests/test_netexec_winrm.py`:

```python
"""Tests for netexec_winrm."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_winrm


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_winrm_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        await netexec_winrm({"target": "10.10.10.5", "action": "check_auth",
                             "username": "jdoe", "password": "x"})


@pytest.mark.asyncio
async def test_winrm_check_auth_success(tmp_targets_dir):
    out = "WINRM    10.10.10.5    5985   DC01   [+] CORP\\jdoe:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_winrm({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)


@pytest.mark.asyncio
async def test_winrm_check_auth_failure_records_invalid(tmp_targets_dir):
    out = "WINRM    10.10.10.5    5985   DC01   [-] CORP\\jdoe:bad\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_winrm({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "bad", "domain": "CORP",
        })
    from reverser.kb import for_target
    invalid = for_target("10.10.10.5").get_credentials(status="invalid")
    assert any(c.username == "jdoe" for c in invalid)


@pytest.mark.asyncio
async def test_winrm_exec_passes_command(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("WINRM    [+] whoami: nt authority\\system\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_winrm({
            "target": "10.10.10.5", "action": "exec",
            "username": "jdoe", "password": "x",
            "command": "whoami",
        })
    assert "-x" in captured["cmd"]
    assert "whoami" in captured["cmd"]


@pytest.mark.asyncio
async def test_winrm_ps_uses_ps_flag(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("[+] ran")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_winrm({
            "target": "10.10.10.5", "action": "ps",
            "username": "jdoe", "password": "x",
            "command": "Get-Process",
        })
    assert "-X" in captured["cmd"]
    assert "Get-Process" in captured["cmd"]


@pytest.mark.asyncio
async def test_winrm_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = await netexec_winrm({
        "target": "10.10.10.5", "action": "spray",
        "username": "jdoe", "password": "Summer2026!",
    })
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_winrm_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = await netexec_winrm({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_winrm_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="jdoe", password="x", status="valid",
    ))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("WINRM    [+] jdoe:x\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        result = await netexec_winrm({"target": "10.10.10.5", "action": "check_auth"})
    assert "jdoe" in captured["cmd"]
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_netexec_winrm.py -v`
Expected: ImportError for `netexec_winrm`.

- [ ] **Step 3: Implement `netexec_winrm`**

Append to `src/reverser/tools/netexec.py`:

```python
# ── netexec_winrm ───────────────────────────────────────────────────

_WINRM_ACTIONS = {"check_auth", "exec", "ps", "spray"}


@tool(
    "netexec_winrm",
    "NetExec WinRM protocol wrapper. Validate credentials, run commands and "
    "PowerShell, or controlled spray. Falls back to KB-stored valid credentials. "
    "Spray actions require REVERSER_AD_ALLOW_SPRAY=1.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target IP, hostname, or CIDR"},
            "action": {"type": "string", "enum": sorted(_WINRM_ACTIONS)},
            "username": {"type": "string", "default": ""},
            "password": {"type": "string", "default": ""},
            "nt_hash": {"type": "string", "default": ""},
            "domain": {"type": "string", "default": ""},
            "command": {"type": "string", "default": "",
                        "description": "Command (action=exec) or PowerShell snippet (action=ps)"},
            "extra_args": {"type": "string", "default": ""},
        },
        "required": ["target", "action"],
    },
)
async def netexec_winrm(args: dict) -> dict:
    from ..kb import (
        for_target, require_pentest_auth,
        CredentialFact, CredResult,
    )
    require_pentest_auth()

    target = args["target"]
    action = args["action"]
    if action not in _WINRM_ACTIONS:
        return format_error(f"Unknown WinRM action: {action}. Valid: {sorted(_WINRM_ACTIONS)}")

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    if action == "spray":
        spray_err = _check_spray_allowed()
        if spray_err:
            return format_error(spray_err)

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)

    cmd: list[str] = ["nxc", "winrm", target]
    cmd.extend(_build_auth_args(cred))

    if action == "exec":
        if not command:
            return format_error("action=exec requires command argument")
        cmd.extend(["-x", command])
    elif action == "ps":
        if not command:
            return format_error("action=ps requires command argument")
        cmd.extend(["-X", command])
    elif action == "spray":
        cmd.extend(["--max-failed-logins", str(_spray_max())])
    # check_auth: nothing extra

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    timeout = NXC_TIMEOUT_MEDIUM if action in ("exec", "ps") else NXC_TIMEOUT_FAST
    result = run_cmd(cmd, timeout=timeout, max_output=16000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)
    if cred.username and (cred.password is not None or cred.nt_hash):
        try:
            status = "valid" if success else "invalid"
            cred_id = kb.record_credential(CredentialFact(
                username=cred.username, password=cred.password, nt_hash=cred.nt_hash,
                domain=cred.domain, source_tool="netexec_winrm", status=status,
            ))
            kb.record_cred_result(cred_id, CredResult(
                service_kind="winrm", target_host=target, success=success,
                error_msg=None if success else (result.get("stderr") or "auth failed")[:500],
            ))
        except Exception as e:
            logger.warning("KB cred-write failed in netexec_winrm: %s", e)

    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc winrm failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_winrm)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_netexec_winrm.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_winrm.py
git commit -m "feat(netexec): netexec_winrm with check_auth, exec, ps, spray"
```

---

## Task 5: netexec_ldap tool

**Files:**
- Modify: `src/reverser/tools/netexec.py` (append `netexec_ldap`)
- Create: `tests/test_netexec_ldap.py`

- [ ] **Step 1: Write failing tests**

`tests/test_netexec_ldap.py`:

```python
"""Tests for netexec_ldap."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_ldap


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_ldap_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        await netexec_ldap({"target": "10.10.10.5", "action": "users",
                            "username": "jdoe", "password": "x"})


@pytest.mark.asyncio
async def test_ldap_check_auth_success(tmp_targets_dir):
    out = "LDAP    10.10.10.5    389   DC01   [+] CORP.LOCAL\\jdoe:Summer2026!\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_ldap({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP.LOCAL",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)


@pytest.mark.asyncio
async def test_ldap_users_action_uses_flag(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("LDAP    [+] jdoe:x\nLDAP    user: alice\nLDAP    user: bob\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_ldap({
            "target": "10.10.10.5", "action": "users",
            "username": "jdoe", "password": "x", "domain": "CORP.LOCAL",
        })
    assert "--users" in captured["cmd"]


@pytest.mark.asyncio
async def test_ldap_computers_records_hosts(tmp_targets_dir):
    out = (
        "LDAP    10.10.10.5    389   DC01   [+] CORP.LOCAL\\jdoe:x\n"
        "LDAP    10.10.10.5    389   DC01   DC01.CORP.LOCAL\n"
        "LDAP    10.10.10.6    389   DC01   WS01.CORP.LOCAL\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_ldap({
            "target": "10.10.10.5", "action": "computers",
            "username": "jdoe", "password": "x", "domain": "CORP.LOCAL",
        })
    from reverser.kb import for_target
    hosts = for_target("10.10.10.5").get_hosts()
    ips = {h.ip for h in hosts}
    assert "10.10.10.5" in ips
    assert "10.10.10.6" in ips
    by_ip = {h.ip: h for h in hosts}
    assert by_ip["10.10.10.5"].hostname == "DC01"
    assert by_ip["10.10.10.5"].domain == "CORP.LOCAL"


@pytest.mark.asyncio
async def test_ldap_kerberoastable_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("LDAP    [+] jdoe:x\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_ldap({
            "target": "10.10.10.5", "action": "kerberoastable",
            "username": "jdoe", "password": "x",
        })
    assert "--kerberoasting" in captured["cmd"] or "kerberoastable" in " ".join(captured["cmd"])


@pytest.mark.asyncio
async def test_ldap_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = await netexec_ldap({"target": "10.10.10.5", "action": "users"})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_ldap_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="jdoe", password="x", domain="CORP.LOCAL", status="valid",
    ))
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok("LDAP    [+] ok\n")):
        result = await netexec_ldap({"target": "10.10.10.5", "action": "users"})
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_netexec_ldap.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `netexec_ldap`**

Append to `src/reverser/tools/netexec.py`:

```python
# ── netexec_ldap ────────────────────────────────────────────────────

_LDAP_ACTIONS = {
    "check_auth", "users", "groups", "computers", "trusts", "gmsa",
    "asreproastable", "kerberoastable", "dc_list", "active_users",
    "admin_count", "password_not_required",
}

_LDAP_ACTION_TO_FLAG = {
    "users": ["--users"],
    "groups": ["--groups"],
    "computers": ["--computers"],
    "trusts": ["--trusted-for-delegation"],
    "gmsa": ["--gmsa"],
    "asreproastable": ["--asreproast", "/dev/null"],
    "kerberoastable": ["--kerberoasting", "/dev/null"],
    "dc_list": ["--dc-list"],
    "active_users": ["--active-users"],
    "admin_count": ["--admin-count"],
    "password_not_required": ["--password-not-required"],
}


@tool(
    "netexec_ldap",
    "NetExec LDAP protocol wrapper. Enumerate users/groups/computers/trusts/GMSA, "
    "find AS-REP roastable / kerberoastable / password-not-required accounts, list "
    "DCs. Falls back to KB-stored valid credentials.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "action": {"type": "string", "enum": sorted(_LDAP_ACTIONS)},
            "username": {"type": "string", "default": ""},
            "password": {"type": "string", "default": ""},
            "nt_hash": {"type": "string", "default": ""},
            "domain": {"type": "string", "default": ""},
            "extra_args": {"type": "string", "default": ""},
        },
        "required": ["target", "action"],
    },
)
async def netexec_ldap(args: dict) -> dict:
    from ..kb import (
        for_target, require_pentest_auth,
        HostFact, CredentialFact, CredResult,
    )
    require_pentest_auth()

    target = args["target"]
    action = args["action"]
    if action not in _LDAP_ACTIONS:
        return format_error(f"Unknown LDAP action: {action}. Valid: {sorted(_LDAP_ACTIONS)}")

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    extra_args = args.get("extra_args", "") or ""

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)

    cmd: list[str] = ["nxc", "ldap", target]
    cmd.extend(_build_auth_args(cred))

    if action in _LDAP_ACTION_TO_FLAG:
        cmd.extend(_LDAP_ACTION_TO_FLAG[action])
    # check_auth uses no extra flags

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    timeout = NXC_TIMEOUT_MEDIUM if action in ("computers", "users", "groups") else NXC_TIMEOUT_FAST
    result = run_cmd(cmd, timeout=timeout, max_output=32000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)

    # Credential lifecycle
    if cred.username and (cred.password is not None or cred.nt_hash):
        try:
            status = "valid" if success else "invalid"
            cred_id = kb.record_credential(CredentialFact(
                username=cred.username, password=cred.password, nt_hash=cred.nt_hash,
                domain=cred.domain, source_tool="netexec_ldap", status=status,
            ))
            kb.record_cred_result(cred_id, CredResult(
                service_kind="ldap", target_host=target, success=success,
                error_msg=None if success else (result.get("stderr") or "auth failed")[:500],
            ))
        except Exception as e:
            logger.warning("KB cred-write failed in netexec_ldap: %s", e)

    # Computers → record_host for each entry
    if action == "computers" and stdout:
        try:
            for c in _parse_nxc_ldap_computers(stdout):
                kb.record_host(HostFact(
                    ip=c["ip"], hostname=c["hostname"], domain=c["domain"],
                ))
        except Exception as e:
            logger.warning("KB host-write failed in netexec_ldap: %s", e)

    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc ldap failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_ldap)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_netexec_ldap.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_ldap.py
git commit -m "feat(netexec): netexec_ldap with computer/user/group enum + roast hunts"
```

---

## Task 6: netexec_mssql tool

**Files:**
- Modify: `src/reverser/tools/netexec.py` (append `netexec_mssql`)
- Create: `tests/test_netexec_mssql.py`

- [ ] **Step 1: Write failing tests**

`tests/test_netexec_mssql.py`:

```python
"""Tests for netexec_mssql."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_mssql


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_mssql_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        await netexec_mssql({"target": "10.10.10.5", "action": "check_auth",
                             "username": "sa", "password": "x"})


@pytest.mark.asyncio
async def test_mssql_check_auth_success(tmp_targets_dir):
    out = "MSSQL    10.10.10.5    1433   DC01   [+] CORP\\sa:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_mssql({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "sa", "password": "Summer2026!", "domain": "CORP",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "sa" for c in valid)


@pytest.mark.asyncio
async def test_mssql_databases_uses_query(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("MSSQL    [+] sa:x\n[*] master\n[*] tempdb\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_mssql({
            "target": "10.10.10.5", "action": "databases",
            "username": "sa", "password": "x", "local_auth": True,
        })
    cmd_str = " ".join(captured["cmd"])
    assert "-q" in captured["cmd"] or "--query" in captured["cmd"] or "sp_databases" in cmd_str.lower()


@pytest.mark.asyncio
async def test_mssql_xp_cmdshell(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("MSSQL    [+] command output\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_mssql({
            "target": "10.10.10.5", "action": "xp_cmdshell",
            "username": "sa", "password": "x", "local_auth": True,
            "command": "whoami",
        })
    assert "-x" in captured["cmd"]
    assert "whoami" in captured["cmd"]


@pytest.mark.asyncio
async def test_mssql_query_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("MSSQL    [+] result row\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_mssql({
            "target": "10.10.10.5", "action": "query",
            "username": "sa", "password": "x", "local_auth": True,
            "query": "SELECT @@version",
        })
    assert "-q" in captured["cmd"] or "--query" in captured["cmd"]
    assert "SELECT @@version" in captured["cmd"]


@pytest.mark.asyncio
async def test_mssql_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = await netexec_mssql({
        "target": "10.10.10.5", "action": "spray",
        "username": "sa", "password": "x",
    })
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_mssql_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = await netexec_mssql({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_mssql_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="sa", password="x", status="valid",
    ))
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok("MSSQL    [+] sa:x\n")):
        result = await netexec_mssql({"target": "10.10.10.5", "action": "check_auth"})
    assert "[KB] Using credential: sa" in result["content"][0]["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_netexec_mssql.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `netexec_mssql`**

Append to `src/reverser/tools/netexec.py`:

```python
# ── netexec_mssql ───────────────────────────────────────────────────

_MSSQL_ACTIONS = {"check_auth", "databases", "xp_cmdshell", "query", "spray"}


@tool(
    "netexec_mssql",
    "NetExec MSSQL protocol wrapper. Validate credentials, list databases, run "
    "queries or xp_cmdshell, controlled spray. Falls back to KB-stored valid "
    "credentials. Spray actions require REVERSER_AD_ALLOW_SPRAY=1.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "action": {"type": "string", "enum": sorted(_MSSQL_ACTIONS)},
            "username": {"type": "string", "default": ""},
            "password": {"type": "string", "default": ""},
            "nt_hash": {"type": "string", "default": ""},
            "domain": {"type": "string", "default": ""},
            "local_auth": {"type": "boolean", "default": False},
            "query": {"type": "string", "default": "",
                      "description": "SQL query (action=query)"},
            "command": {"type": "string", "default": "",
                        "description": "OS command (action=xp_cmdshell)"},
            "extra_args": {"type": "string", "default": ""},
        },
        "required": ["target", "action"],
    },
)
async def netexec_mssql(args: dict) -> dict:
    from ..kb import (
        for_target, require_pentest_auth,
        CredentialFact, CredResult,
    )
    require_pentest_auth()

    target = args["target"]
    action = args["action"]
    if action not in _MSSQL_ACTIONS:
        return format_error(f"Unknown MSSQL action: {action}. Valid: {sorted(_MSSQL_ACTIONS)}")

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    local_auth = bool(args.get("local_auth", False))
    query = args.get("query", "") or ""
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    if action == "spray":
        spray_err = _check_spray_allowed()
        if spray_err:
            return format_error(spray_err)

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)

    cmd: list[str] = ["nxc", "mssql", target]
    cmd.extend(_build_auth_args(cred, local_auth=local_auth))

    if action == "databases":
        # nxc exposes -q for raw query; sp_databases lists DB names.
        cmd.extend(["-q", "EXEC sp_databases"])
    elif action == "xp_cmdshell":
        if not command:
            return format_error("action=xp_cmdshell requires command argument")
        cmd.extend(["-x", command])
    elif action == "query":
        if not query:
            return format_error("action=query requires query argument")
        cmd.extend(["-q", query])
    elif action == "spray":
        cmd.extend(["--max-failed-logins", str(_spray_max())])
    # check_auth: nothing extra

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    result = run_cmd(cmd, timeout=NXC_TIMEOUT_FAST, max_output=16000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)
    if cred.username and (cred.password is not None or cred.nt_hash):
        try:
            status = "valid" if success else "invalid"
            cred_id = kb.record_credential(CredentialFact(
                username=cred.username, password=cred.password, nt_hash=cred.nt_hash,
                domain=cred.domain, source_tool="netexec_mssql", status=status,
            ))
            kb.record_cred_result(cred_id, CredResult(
                service_kind="mssql", target_host=target, success=success,
                error_msg=None if success else (result.get("stderr") or "auth failed")[:500],
            ))
        except Exception as e:
            logger.warning("KB cred-write failed in netexec_mssql: %s", e)

    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc mssql failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_mssql)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_netexec_mssql.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_mssql.py
git commit -m "feat(netexec): netexec_mssql with databases/query/xp_cmdshell/spray"
```

---

## Task 7: netexec_ssh tool

**Files:**
- Modify: `src/reverser/tools/netexec.py` (append `netexec_ssh`)
- Create: `tests/test_netexec_ssh.py`

- [ ] **Step 1: Write failing tests**

`tests/test_netexec_ssh.py`:

```python
"""Tests for netexec_ssh."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_ssh


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_ssh_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        await netexec_ssh({"target": "10.10.10.5", "action": "check_auth",
                           "username": "root", "password": "x"})


@pytest.mark.asyncio
async def test_ssh_check_auth_success(tmp_targets_dir):
    out = "SSH    10.10.10.5    22   ubuntu   [+] root:Summer2026!\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_ssh({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "root", "password": "Summer2026!",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "root" for c in valid)


@pytest.mark.asyncio
async def test_ssh_check_auth_failure_records_invalid(tmp_targets_dir):
    out = "SSH    10.10.10.5    22   ubuntu   [-] root:bad\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_ssh({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "root", "password": "bad",
        })
    from reverser.kb import for_target
    invalid = for_target("10.10.10.5").get_credentials(status="invalid")
    assert any(c.username == "root" for c in invalid)


@pytest.mark.asyncio
async def test_ssh_key_file_passed(tmp_targets_dir, tmp_path):
    keyfile = tmp_path / "id_rsa"
    keyfile.write_text("KEY")
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("SSH    [+] root:KEY\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_ssh({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "root", "key_file": str(keyfile),
        })
    cmd_str = " ".join(captured["cmd"])
    assert str(keyfile) in cmd_str


@pytest.mark.asyncio
async def test_ssh_exec_passes_command(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("SSH    [+] uid=0\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_ssh({
            "target": "10.10.10.5", "action": "exec",
            "username": "root", "password": "x", "command": "id",
        })
    assert "-x" in captured["cmd"]
    assert "id" in captured["cmd"]


@pytest.mark.asyncio
async def test_ssh_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = await netexec_ssh({
        "target": "10.10.10.5", "action": "spray",
        "username": "root", "password": "x",
    })
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_ssh_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = await netexec_ssh({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_ssh_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="ubuntu", password="x", status="valid",
    ))
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok("SSH    [+] ok\n")):
        result = await netexec_ssh({"target": "10.10.10.5", "action": "check_auth"})
    assert "[KB] Using credential: ubuntu" in result["content"][0]["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_netexec_ssh.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `netexec_ssh`**

Append to `src/reverser/tools/netexec.py`:

```python
# ── netexec_ssh ─────────────────────────────────────────────────────

_SSH_ACTIONS = {"check_auth", "exec", "spray"}


@tool(
    "netexec_ssh",
    "NetExec SSH protocol wrapper. Validate credentials (password or key), run "
    "commands, controlled spray. Falls back to KB-stored valid credentials. "
    "Spray actions require REVERSER_AD_ALLOW_SPRAY=1.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "action": {"type": "string", "enum": sorted(_SSH_ACTIONS)},
            "username": {"type": "string", "default": ""},
            "password": {"type": "string", "default": ""},
            "key_file": {"type": "string", "default": "",
                         "description": "Path to private key file (alternative to password)"},
            "command": {"type": "string", "default": ""},
            "extra_args": {"type": "string", "default": ""},
        },
        "required": ["target", "action"],
    },
)
async def netexec_ssh(args: dict) -> dict:
    from ..kb import (
        for_target, require_pentest_auth,
        CredentialFact, CredResult,
    )
    require_pentest_auth()

    target = args["target"]
    action = args["action"]
    if action not in _SSH_ACTIONS:
        return format_error(f"Unknown SSH action: {action}. Valid: {sorted(_SSH_ACTIONS)}")

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    key_file = args.get("key_file", "") or ""
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    if action == "spray":
        spray_err = _check_spray_allowed()
        if spray_err:
            return format_error(spray_err)

    # SSH allows key-based auth; treat key_file as a "credential present"
    # signal even without password — but still call _resolve_credential for
    # KB fallback when neither password nor key_file is given.
    if key_file and not password:
        if not username:
            return format_error("key_file requires a username")
        cred = ResolvedCredential(
            username=username, password=None, nt_hash=None, domain=None,
            origin=f"explicit args (key={key_file})",
        )
    else:
        cred, err = _resolve_credential(target, username, password, None, None)
        if err:
            return format_error(err)

    cmd: list[str] = ["nxc", "ssh", target]
    if cred.username:
        cmd.extend(["-u", cred.username])
    if cred.password is not None:
        cmd.extend(["-p", cred.password])
    if key_file:
        cmd.extend(["--key-file", key_file])

    if action == "exec":
        if not command:
            return format_error("action=exec requires command argument")
        cmd.extend(["-x", command])
    elif action == "spray":
        cmd.extend(["--max-failed-logins", str(_spray_max())])

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    result = run_cmd(cmd, timeout=NXC_TIMEOUT_FAST, max_output=16000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)
    if cred.username and (cred.password is not None or key_file):
        try:
            status = "valid" if success else "invalid"
            cred_id = kb.record_credential(CredentialFact(
                username=cred.username, password=cred.password,
                source_tool="netexec_ssh",
                source_context=f"key={key_file}" if key_file else None,
                status=status,
            ))
            kb.record_cred_result(cred_id, CredResult(
                service_kind="ssh", target_host=target, success=success,
                error_msg=None if success else (result.get("stderr") or "auth failed")[:500],
            ))
        except Exception as e:
            logger.warning("KB cred-write failed in netexec_ssh: %s", e)

    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc ssh failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_ssh)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_netexec_ssh.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_ssh.py
git commit -m "feat(netexec): netexec_ssh with password/key auth + exec + spray"
```

---

## Task 8: netexec_ftp_wmi tool

**Files:**
- Modify: `src/reverser/tools/netexec.py` (append `netexec_ftp_wmi`)
- Create: `tests/test_netexec_ftp_wmi.py`

- [ ] **Step 1: Write failing tests**

`tests/test_netexec_ftp_wmi.py`:

```python
"""Tests for netexec_ftp_wmi."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_ftp_wmi


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_ftp_wmi_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        await netexec_ftp_wmi({"target": "10.10.10.5", "protocol": "ftp",
                               "action": "check_auth",
                               "username": "anonymous", "password": "anonymous"})


@pytest.mark.asyncio
async def test_ftp_check_auth_success(tmp_targets_dir):
    out = "FTP    10.10.10.5    21   FTP-SVR   [+] anonymous:anonymous\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_ftp_wmi({
            "target": "10.10.10.5", "protocol": "ftp", "action": "check_auth",
            "username": "anonymous", "password": "anonymous",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "anonymous" for c in valid)


@pytest.mark.asyncio
async def test_ftp_list_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("FTP    [+] ok\nFTP    file1.txt\nFTP    file2.txt\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_ftp_wmi({
            "target": "10.10.10.5", "protocol": "ftp", "action": "list",
            "username": "anonymous", "password": "anonymous",
        })
    cmd_str = " ".join(captured["cmd"])
    assert "ls" in cmd_str.lower() or "--ls" in cmd_str


@pytest.mark.asyncio
async def test_wmi_exec_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("WMI    10.10.10.5    135   DC01   [+] command output\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        await netexec_ftp_wmi({
            "target": "10.10.10.5", "protocol": "wmi", "action": "exec",
            "username": "jdoe", "password": "x", "command": "whoami",
        })
    assert "wmi" in captured["cmd"]
    assert "-x" in captured["cmd"]
    assert "whoami" in captured["cmd"]


@pytest.mark.asyncio
async def test_wmi_check_auth_records_creds(tmp_targets_dir):
    out = "WMI    10.10.10.5    135   DC01   [+] CORP\\jdoe:Summer2026!\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        await netexec_ftp_wmi({
            "target": "10.10.10.5", "protocol": "wmi", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)


@pytest.mark.asyncio
async def test_invalid_protocol_returns_error(tmp_targets_dir):
    result = await netexec_ftp_wmi({
        "target": "10.10.10.5", "protocol": "rdp", "action": "check_auth",
        "username": "x", "password": "y",
    })
    assert result.get("is_error") is True
    assert "protocol" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = await netexec_ftp_wmi({
        "target": "10.10.10.5", "protocol": "ftp", "action": "check_auth",
    })
    assert result.get("is_error") is True


@pytest.mark.asyncio
async def test_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="anonymous", password="anonymous", status="valid",
    ))
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok("FTP    [+] ok\n")):
        result = await netexec_ftp_wmi({
            "target": "10.10.10.5", "protocol": "ftp", "action": "check_auth",
        })
    assert "[KB] Using credential: anonymous" in result["content"][0]["text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_netexec_ftp_wmi.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `netexec_ftp_wmi`**

Append to `src/reverser/tools/netexec.py`:

```python
# ── netexec_ftp_wmi ─────────────────────────────────────────────────

_FTP_ACTIONS = {"check_auth", "list", "get"}
_WMI_ACTIONS = {"check_auth", "exec"}
_VALID_PROTOCOLS = {"ftp", "wmi"}


@tool(
    "netexec_ftp_wmi",
    "NetExec wrapper for FTP and WMI protocols. FTP: check_auth, list directories, "
    "download files. WMI: check_auth, run remote commands. Falls back to KB-stored "
    "valid credentials.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "protocol": {"type": "string", "enum": sorted(_VALID_PROTOCOLS)},
            "action": {
                "type": "string",
                "enum": sorted(_FTP_ACTIONS | _WMI_ACTIONS),
                "description": "ftp: check_auth|list|get; wmi: check_auth|exec",
            },
            "username": {"type": "string", "default": ""},
            "password": {"type": "string", "default": ""},
            "nt_hash": {"type": "string", "default": ""},
            "domain": {"type": "string", "default": ""},
            "command": {"type": "string", "default": "",
                        "description": "wmi: command to run; ftp/get: remote path; ftp/list: dir path"},
            "extra_args": {"type": "string", "default": ""},
        },
        "required": ["target", "protocol", "action"],
    },
)
async def netexec_ftp_wmi(args: dict) -> dict:
    from ..kb import (
        for_target, require_pentest_auth,
        CredentialFact, CredResult,
    )
    require_pentest_auth()

    target = args["target"]
    protocol = args["protocol"]
    action = args["action"]

    if protocol not in _VALID_PROTOCOLS:
        return format_error(
            f"Unknown protocol: {protocol}. Valid: {sorted(_VALID_PROTOCOLS)}"
        )

    valid_actions = _FTP_ACTIONS if protocol == "ftp" else _WMI_ACTIONS
    if action not in valid_actions:
        return format_error(
            f"Unknown action {action!r} for protocol {protocol}. "
            f"Valid: {sorted(valid_actions)}"
        )

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)

    cmd: list[str] = ["nxc", protocol, target]
    cmd.extend(_build_auth_args(cred))

    if protocol == "ftp":
        if action == "list":
            # nxc ftp uses --ls <path>
            ls_path = command or "/"
            cmd.extend(["--ls", ls_path])
        elif action == "get":
            if not command:
                return format_error("ftp/get requires command argument (remote path)")
            cmd.extend(["--get", command])
        # check_auth: no extra
    elif protocol == "wmi":
        if action == "exec":
            if not command:
                return format_error("wmi/exec requires command argument")
            cmd.extend(["-x", command])
        # check_auth: no extra

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    result = run_cmd(cmd, timeout=NXC_TIMEOUT_FAST, max_output=16000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)
    if cred.username and (cred.password is not None or cred.nt_hash):
        try:
            status = "valid" if success else "invalid"
            cred_id = kb.record_credential(CredentialFact(
                username=cred.username, password=cred.password, nt_hash=cred.nt_hash,
                domain=cred.domain, source_tool=f"netexec_{protocol}",
                status=status,
            ))
            kb.record_cred_result(cred_id, CredResult(
                service_kind=protocol, target_host=target, success=success,
                error_msg=None if success else (result.get("stderr") or "auth failed")[:500],
            ))
        except Exception as e:
            logger.warning("KB cred-write failed in netexec_ftp_wmi: %s", e)

    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc {protocol} failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_ftp_wmi)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_netexec_ftp_wmi.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/netexec.py tests/test_netexec_ftp_wmi.py
git commit -m "feat(netexec): netexec_ftp_wmi unified FTP+WMI wrapper"
```

---

## Task 9: Register netexec tools in `tools/__init__.py`

**Files:**
- Modify: `src/reverser/tools/__init__.py`

- [ ] **Step 1: Write a failing assertion test**

Create `tests/test_tool_registry.py`:

```python
"""Verify netexec tools are exposed via the MCP server registry."""

from reverser.tools import ALL_TOOLS


def test_all_six_netexec_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert "netexec_smb" in names
    assert "netexec_winrm" in names
    assert "netexec_ldap" in names
    assert "netexec_mssql" in names
    assert "netexec_ssh" in names
    assert "netexec_ftp_wmi" in names


def test_netexec_tools_count():
    netexec_names = {t.name for t in ALL_TOOLS if t.name.startswith("netexec_")}
    assert len(netexec_names) == 6
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_tool_registry.py -v`
Expected: 2 failures (`netexec_smb` not in names) — module not yet imported by registry.

- [ ] **Step 3: Modify `src/reverser/tools/__init__.py`**

Update the file to import the new module:

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
from .netexec import TOOLS as netexec_tools

ALL_TOOLS = (
    triage_tools + static_tools + dynamic_tools + python_tools
    + exploit_tools + util_tools + network_tools + web_tools
    + netexec_tools
)


def create_re_mcp_server():
    """Create the MCP server exposing all RE tools."""
    return create_sdk_mcp_server(
        name="re",
        version="0.1.0",
        tools=ALL_TOOLS,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_tool_registry.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest -v`
Expected: all KB tests from Plan 1 + all netexec tests from Plan 3 pass. No failures.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tools/__init__.py tests/test_tool_registry.py
git commit -m "feat(tools): register netexec tools in MCP server"
```

---

## Task 10: Cross-tool integration smoke test

**Files:**
- Create: `tests/test_netexec_integration.py`

- [ ] **Step 1: Write the integration smoke test**

`tests/test_netexec_integration.py`:

```python
"""End-to-end smoke: simulate an engagement that walks through all 6 netexec tools.

All subprocess calls are mocked. The test verifies KB state after each step
matches what the AD profile prompt expects (creds propagate, hosts get recorded
from LDAP enum, dumps land in loot/, etc.).
"""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import (
    netexec_smb, netexec_winrm, netexec_ldap,
    netexec_mssql, netexec_ssh, netexec_ftp_wmi,
)


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


@pytest.mark.asyncio
async def test_full_engagement_walkthrough(tmp_targets_dir):
    target = "10.10.10.5"

    # Step 1: SMB check_auth with a found credential
    smb_out = "SMB    10.10.10.5    445   DC01   [+] CORP\\jdoe:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(smb_out)):
        await netexec_smb({
            "target": target, "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })

    from reverser.kb import for_target
    kb = for_target(target)
    valid = kb.get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid), "SMB check_auth must record valid cred"

    # Step 2: SMB shares — KB fallback should kick in (no creds passed)
    shares_out = (
        "SMB    10.10.10.5    445   DC01   [+] CORP\\jdoe:Summer2026!\n"
        "SMB    10.10.10.5    445   DC01   Share           Permissions     Remark\n"
        "SMB    10.10.10.5    445   DC01   -----           -----------     ------\n"
        "SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin\n"
        "SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(shares_out)):
        result = await netexec_smb({"target": target, "action": "shares"})
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]
    assert any("ADMIN$" in n for n in kb.get_notes())

    # Step 3: LDAP computers — should record_host for both
    ldap_out = (
        "LDAP    10.10.10.5    389   DC01   [+] CORP.LOCAL\\jdoe:Summer2026!\n"
        "LDAP    10.10.10.5    389   DC01   DC01.CORP.LOCAL\n"
        "LDAP    10.10.10.6    389   DC01   WS01.CORP.LOCAL\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(ldap_out)):
        await netexec_ldap({"target": target, "action": "computers"})
    hosts = kb.get_hosts()
    ips = {h.ip for h in hosts}
    assert "10.10.10.5" in ips
    assert "10.10.10.6" in ips

    # Step 4: WinRM check_auth — KB cred used again, recorded as valid for winrm
    winrm_out = "WINRM    10.10.10.5    5985   DC01   [+] CORP\\jdoe:Summer2026!\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(winrm_out)):
        await netexec_winrm({"target": target, "action": "check_auth"})

    # Step 5: SSH check_auth — different user, different protocol
    ssh_out = "SSH    10.10.10.5    22   ubuntu   [+] root:rootpw\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(ssh_out)):
        await netexec_ssh({
            "target": target, "action": "check_auth",
            "username": "root", "password": "rootpw",
        })

    # Step 6: MSSQL check_auth (failure)
    mssql_out = "MSSQL    10.10.10.5    1433   DC01   [-] sa:bad STATUS_LOGIN_FAILURE\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(mssql_out)):
        await netexec_mssql({
            "target": target, "action": "check_auth",
            "username": "sa", "password": "bad",
        })
    invalid = kb.get_credentials(status="invalid")
    assert any(c.username == "sa" for c in invalid)

    # Step 7: FTP anonymous check
    ftp_out = "FTP    10.10.10.5    21   FTP   [+] anonymous:anonymous\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(ftp_out)):
        await netexec_ftp_wmi({
            "target": target, "protocol": "ftp", "action": "check_auth",
            "username": "anonymous", "password": "anonymous",
        })

    # Step 8: SMB ntds dump → should save artifact + record extracted hashes
    ntds_out = (
        "SMB    10.10.10.5    445   DC01   [+] CORP\\jdoe:Summer2026!\n"
        "SMB    10.10.10.5    445   DC01   [+] Dumping NTDS\n"
        "Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::\n"
        "krbtgt:502:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(ntds_out)):
        await netexec_smb({
            "target": target, "action": "ntds",
            "username": "Administrator", "nt_hash": "8846f7eaee8fb117ad06bdd830b7586c",
            "local_auth": True,
        })
    artifacts = kb.get_artifacts()
    assert any(a.kind == "ntds_dump" for a in artifacts)
    untested = kb.get_credentials(status="untested")
    untested_users = {c.username for c in untested}
    assert "krbtgt" in untested_users

    # Final state inspection: 4 valid creds (jdoe via smb/winrm/ldap, root via ssh,
    # anonymous via ftp, Administrator via smb-ntds-auth), 1 invalid (sa),
    # 2 untested (Administrator+krbtgt from ntds parsing). Note: jdoe is
    # deduped to a single row.
    all_creds = kb.get_credentials()
    by_user = {c.username for c in all_creds}
    assert "jdoe" in by_user
    assert "root" in by_user
    assert "anonymous" in by_user
    assert "sa" in by_user
    assert "Administrator" in by_user
    assert "krbtgt" in by_user
```

- [ ] **Step 2: Run to verify pass**

Run: `pytest tests/test_netexec_integration.py -v`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_netexec_integration.py
git commit -m "test(netexec): end-to-end engagement walkthrough across all 6 tools"
```

---

## Task 11: Final regression sweep + tool count verification

**Files:**
- (none — verification only, optional cleanup commits)

- [ ] **Step 1: Run the full suite**

Run: `pytest -v`
Expected: all KB tests (Plan 1) + all netexec tests pass. Note the totals so the next plan can regression-check.

Approximate counts to expect:
- Plan 1: ~37 tests (kb_authz + kb_schema + kb_store + kb_integration)
- Plan 3 helpers: 21 tests
- Plan 3 per-protocol tools: 10 + 8 + 7 + 8 + 8 + 8 = 49 tests
- Plan 3 registry: 2 tests
- Plan 3 integration: 1 test
- **Plan 3 total: ~73 tests added on top of Plan 1's ~37 = ~110 total**

- [ ] **Step 2: Verify the public tool surface**

Run a quick interactive check:

```bash
python -c "from reverser.tools import ALL_TOOLS; \
  print('netexec tools:', [t.name for t in ALL_TOOLS if t.name.startswith('netexec_')])"
```

Expected output: a list of the 6 names — `netexec_smb`, `netexec_winrm`, `netexec_ldap`, `netexec_mssql`, `netexec_ssh`, `netexec_ftp_wmi`.

- [ ] **Step 3: Smoke-test the actual `nxc` binary (optional, requires devenv shell)**

Run: `nxc smb --help | head -20`
Expected: NetExec usage banner. Confirms the binary is wired up. If this fails, the tools will still pass unit tests but will fail at runtime — fix `devenv.nix` before declaring done.

- [ ] **Step 4: Final cleanup commit (only if needed)**

If any cosmetic cleanup was performed during the sweep, commit it:

```bash
git commit -am "chore(netexec): final cleanup pass"
```

Otherwise skip.

---

## Done

Plan 3 ships 6 NetExec wrapper tools that:
- enforce pentest authorization
- transparently fall back to KB-stored valid credentials
- record every auth attempt's outcome to the KB credential lifecycle
- record extracted hashes and dump files as artifacts (sam/lsa/ntds)
- record discovered hosts (LDAP /computers) and shares (notes)
- gate spray actions behind `REVERSER_AD_ALLOW_SPRAY` and cap attempts via `REVERSER_SPRAY_MAX`
- expose via `create_re_mcp_server()` alongside the existing tools

Next up: **Plan 4 — BloodHound stack (Neo4j lifecycle + collector + canned cypher + free-form cypher).**
