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
    result = run_cmd(cmd, timeout=timeout, max_output=32000)
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
