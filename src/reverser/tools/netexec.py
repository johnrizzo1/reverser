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
    arun_cmd,
    cmd_result_to_tool_result,
    format_error,
    format_tool_result,
)

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────

DEFAULT_SPRAY_MAX = 3
NXC_TIMEOUT_FAST = 60
NXC_TIMEOUT_MEDIUM = 180
NXC_TIMEOUT_SLOW = 600


# ── Credential fallback ─────────────────────────────────────────────

@dataclass
class ResolvedCredential:
    username: Optional[str]
    password: Optional[str]
    nt_hash: Optional[str]
    domain: Optional[str]
    origin: str


def _resolve_credential(
    target: str,
    username: Optional[str],
    password: Optional[str],
    nt_hash: Optional[str],
    domain: Optional[str],
) -> tuple[Optional[ResolvedCredential], Optional[str]]:
    """Resolve credentials, falling back to KB if all auth args are empty."""
    if username or password or nt_hash:
        return ResolvedCredential(
            username=username or None,
            password=password or None,
            nt_hash=nt_hash or None,
            domain=domain or None,
            origin="explicit args",
        ), None

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
    if os.environ.get("REVERSER_AD_ALLOW_SPRAY") != "1":
        return (
            "Spray actions are disabled. Set REVERSER_AD_ALLOW_SPRAY=1 to enable. "
            "Spray can lock out accounts; only enable after confirming the engagement "
            "rules-of-engagement permit it. The hard cap REVERSER_SPRAY_MAX (default "
            f"{DEFAULT_SPRAY_MAX}) limits attempts per user even when enabled."
        )
    return None


def _spray_max() -> int:
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
    from ..kb import for_target
    kb = for_target(target)
    loot_dir = kb.root / "loot"
    loot_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{kind}_{_timestamp()}.txt"
    path = loot_dir / fname
    path.write_text(content, encoding="utf-8", errors="replace")
    return path, _sha256_of_text(content)


# ── NetExec output parsers ──────────────────────────────────────────

_NXC_STATUS_RE = re.compile(
    r"^\s*(?P<proto>\S+)\s+(?P<ip>\S+)\s+(?P<port>\d+)\s+(?P<host>\S+)\s+"
    r"\[(?P<sign>[+\-*!])\]\s*(?P<rest>.*)$"
)


def _parse_nxc_status_line(line: str) -> Optional[dict]:
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
    for line in stdout.splitlines():
        parsed = _parse_nxc_status_line(line)
        if parsed and parsed["sign"] == "+":
            return True
    return False


_SHARE_ROW_RE = re.compile(
    r"^\s*SMB\s+\S+\s+\d+\s+\S+\s+(?P<share>\S+)\s+(?P<perms>[A-Z,]*)\s*(?P<remark>.*)$"
)


def _parse_nxc_share_table(stdout: str) -> list[dict]:
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


_LDAP_COMPUTER_RE = re.compile(
    r"^\s*LDAP\s+(?P<ip>\S+)\s+\d+\s+\S+\s+(?P<fqdn>[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)\s*$"
)


def _parse_nxc_ldap_computers(stdout: str) -> list[dict]:
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


_PWDUMP_RE = re.compile(
    r"^(?P<user>[^:\s][^:]*):(?P<rid>\d+):(?P<lm>[a-fA-F0-9]{32}):(?P<nt>[a-fA-F0-9]{32}):"
)


def _parse_nxc_secret_dump(stdout: str) -> list[dict]:
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


TOOLS: list = []


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

    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
            if action == "spray":
                scope.assert_spray_allowed()
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")
    # ────────────────────────────────────────────────────────────────

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    local_auth = bool(args.get("local_auth", False))
    module = (args.get("module", "") or "").strip()
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    if action == "spray":
        spray_err = _check_spray_allowed()
        if spray_err:
            return format_error(spray_err)

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)
    assert cred is not None  # _resolve_credential contract: cred non-None when err is None

    cmd: list[str] = ["nxc", "smb", target]
    cmd.extend(_build_auth_args(cred, local_auth=local_auth))

    if action in _SMB_ACTION_TO_FLAG:
        cmd.extend(_SMB_ACTION_TO_FLAG[action])
    elif action == "exec":
        if command:
            cmd.extend(["-x", command])
        elif not module:
            return format_error("action=exec requires either command or module argument")
    elif action == "spray":
        cmd.extend(["--max-failed-logins", str(_spray_max())])
    elif action == "check_auth":
        pass

    if module:
        cmd.extend(["-M", module])

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    timeout = NXC_TIMEOUT_SLOW if action in ("ntds", "lsa", "sam", "spider", "spray") else NXC_TIMEOUT_FAST
    result = await arun_cmd(cmd, timeout=timeout, max_output=32000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)

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

    out_text = f"{cred.origin}\n\n" + stdout
    if result.get("stderr"):
        out_text += f"\n\n[stderr]: {result['stderr'][:500]}"
    if result["returncode"] != 0 and not stdout:
        return format_error(result["stderr"] or f"nxc smb failed (rc={result['returncode']})")
    return format_tool_result(out_text)


TOOLS.append(netexec_smb)


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

    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
            if action == "spray":
                scope.assert_spray_allowed()
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")
    # ────────────────────────────────────────────────────────────────

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
    assert cred is not None

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

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    timeout = NXC_TIMEOUT_MEDIUM if action in ("exec", "ps") else NXC_TIMEOUT_FAST
    result = await arun_cmd(cmd, timeout=timeout, max_output=16000)
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

    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
            if action == "spray":
                scope.assert_spray_allowed()
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")
    # ────────────────────────────────────────────────────────────────

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    extra_args = args.get("extra_args", "") or ""

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)
    assert cred is not None

    cmd: list[str] = ["nxc", "ldap", target]
    cmd.extend(_build_auth_args(cred))

    if action in _LDAP_ACTION_TO_FLAG:
        cmd.extend(_LDAP_ACTION_TO_FLAG[action])

    if extra_args:
        cmd.extend(shlex.split(extra_args))

    timeout = NXC_TIMEOUT_MEDIUM if action in ("computers", "users", "groups") else NXC_TIMEOUT_FAST
    result = await arun_cmd(cmd, timeout=timeout, max_output=32000)
    stdout = result["stdout"]
    success = _auth_succeeded(stdout)

    kb = for_target(target)

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

    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
            if action == "spray":
                scope.assert_spray_allowed()
            if action in ("xp_cmdshell", "query"):
                scope.assert_dos_allowed()
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")
    # ────────────────────────────────────────────────────────────────

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
    assert cred is not None

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

    result = await arun_cmd(cmd, timeout=NXC_TIMEOUT_FAST, max_output=16000)
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

    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
            if action == "spray":
                scope.assert_spray_allowed()
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")
    # ────────────────────────────────────────────────────────────────

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
        assert cred is not None

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

    result = await arun_cmd(cmd, timeout=NXC_TIMEOUT_FAST, max_output=16000)
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

    # ── Scope enforcement (optional; no-op if scope.toml is absent) ──
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")
    # ────────────────────────────────────────────────────────────────

    username = args.get("username", "") or None
    password = args.get("password", "") or None
    nt_hash = args.get("nt_hash", "") or None
    domain = args.get("domain", "") or None
    command = args.get("command", "") or ""
    extra_args = args.get("extra_args", "") or ""

    cred, err = _resolve_credential(target, username, password, nt_hash, domain)
    if err:
        return format_error(err)
    assert cred is not None

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

    result = await arun_cmd(cmd, timeout=NXC_TIMEOUT_FAST, max_output=16000)
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
