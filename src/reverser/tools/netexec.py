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
