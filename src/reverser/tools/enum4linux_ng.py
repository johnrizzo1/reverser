"""enum4linux-ng wrapper — SMB/RPC/LDAP/NetBIOS enumeration with KB integration.

enum4linux-ng is a modern Python rewrite of enum4linux.pl. It enumerates:
- SMB shares
- Users (RID cycling)
- Groups
- Domain info
- OS info
- Password policy
- Sessions
- LDAP (with bind)
- NetBIOS

Usage upstream:
    enum4linux-ng -A <target>           # all simple checks (default)
    enum4linux-ng -As <target>          # quick (no LDAP, no policy)
    enum4linux-ng -U -u admin -p pass <target>  # users with auth

This wrapper:
- requires pentest authorization at function entry
- enforces scope.toml in_scope_cidrs (when present)
- writes JSON output to a tempfile, parses, summarizes, and persists
  domain/host facts and a note containing the share/user summary
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from claude_agent_sdk import tool

from ._common import format_error, format_tool_result, run_cmd

logger = logging.getLogger(__name__)


# enum4linux-ng has predictable timeout characteristics — most checks are
# fast (<30s), LDAP/policy can take longer.
E4L_TIMEOUT_FAST = 60
E4L_TIMEOUT_FULL = 300


_MODE_TO_FLAGS = {
    "all":     ["-A"],          # all simple checks
    "quick":   ["-As"],         # all but LDAP and policy (faster)
    "users":   ["-U"],          # users only (RID cycling)
    "groups":  ["-G"],
    "shares":  ["-S"],
    "policy":  ["-P"],
    "os":      ["-O"],
    "ldap":    ["-L"],
    "netbios": ["-N"],
    "kerberos": ["-K"],
    "sessions": ["-C"],
    "rid":     ["-R"],
}


TOOLS: list = []


@tool(
    "enum4linux_ng",
    "Run enum4linux-ng against a target for SMB/RPC/LDAP/NetBIOS enumeration. "
    "Pulls domain info, user list, share list, password policy, OS info. "
    "Defaults to mode='all' (the -A flag — all simple checks). Pass username/"
    "password for authenticated enumeration (otherwise tries anonymous/null "
    "session). Writes domain + host facts to the per-target KB and a summary "
    "note covering shares + user count.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target IP or hostname"},
            "mode": {
                "type": "string",
                "description": (
                    "all (default; -A: all simple checks), "
                    "quick (-As: no LDAP, no policy), "
                    "users (-U), groups (-G), shares (-S), policy (-P), "
                    "os (-O), ldap (-L), netbios (-N), kerberos (-K), "
                    "sessions (-C), rid (-R: RID cycling)"
                ),
                "default": "all",
                "enum": list(_MODE_TO_FLAGS.keys()),
            },
            "username": {
                "type": "string",
                "description": "Username for authenticated enumeration (omit for anonymous)",
                "default": "",
            },
            "password": {
                "type": "string",
                "description": "Password for authenticated enumeration",
                "default": "",
            },
            "domain": {
                "type": "string",
                "description": "Workgroup / domain name (-w/-d in enum4linux-ng)",
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def enum4linux_ng(args: dict) -> dict:
    from ..kb import for_target, require_pentest_auth, HostFact
    require_pentest_auth()

    target = args["target"]
    mode = args.get("mode", "all")
    username = (args.get("username") or "").strip()
    password = (args.get("password") or "").strip()
    domain = (args.get("domain") or "").strip()

    # Scope enforcement (no-op if no scope.toml)
    from ..kb.scope import load_scope, ScopeError
    scope = load_scope(target)
    if scope is not None:
        try:
            scope.assert_in_scope(target)
        except ScopeError as e:
            return format_error(f"scope.toml violation: {e}")

    if mode not in _MODE_TO_FLAGS:
        return format_error(
            f"Unknown mode: {mode!r}. Valid: {sorted(_MODE_TO_FLAGS.keys())}"
        )

    # enum4linux-ng writes JSON output to a file (no stdout option).
    # Use a tempfile, run, read the JSON back, and summarize.
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        json_path = tf.name

    cmd: list[str] = ["enum4linux-ng"]
    cmd.extend(_MODE_TO_FLAGS[mode])
    cmd.extend(["-oJ", json_path.removesuffix(".json")])  # tool appends .json

    if username:
        cmd.extend(["-u", username])
    if password:
        cmd.extend(["-p", password])
    if domain:
        cmd.extend(["-d", domain])

    cmd.append(target)

    timeout = E4L_TIMEOUT_FULL if mode == "all" else E4L_TIMEOUT_FAST
    result = run_cmd(cmd, timeout=timeout, max_output=32000)
    stdout = result["stdout"] or ""
    stderr = result["stderr"] or ""

    parsed: dict = {}
    json_file = Path(json_path)
    if json_file.exists() and json_file.stat().st_size > 0:
        try:
            parsed = json.loads(json_file.read_text())
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse enum4linux-ng JSON: %s", e)
        finally:
            try:
                json_file.unlink()
            except OSError:
                pass

    # Build a concise summary from the parsed JSON
    summary_lines = [
        f"# enum4linux-ng {target} (mode={mode})",
        f"**Exit code:** {result['returncode']}",
    ]

    if parsed:
        # Domain / OS info
        domain_info = parsed.get("domain_info") or parsed.get("smb_domain_info") or {}
        os_info = parsed.get("os_info") or {}
        if domain_info or os_info:
            summary_lines.append("")
            summary_lines.append("## Host info")
            if isinstance(domain_info, dict):
                for k, v in domain_info.items():
                    if v not in (None, "", []):
                        summary_lines.append(f"- {k}: {v}")
            if isinstance(os_info, dict):
                for k, v in os_info.items():
                    if v not in (None, "", []):
                        summary_lines.append(f"- {k}: {v}")

        # Shares
        shares = parsed.get("shares") or {}
        share_dict = shares.get("shares", shares) if isinstance(shares, dict) else {}
        if share_dict and isinstance(share_dict, dict):
            summary_lines.append("")
            summary_lines.append(f"## Shares ({len(share_dict)})")
            for name, info in list(share_dict.items())[:30]:
                comment = ""
                if isinstance(info, dict):
                    comment = info.get("comment") or info.get("type") or ""
                summary_lines.append(f"- {name}: {comment}")

        # Users
        users = parsed.get("users") or {}
        user_dict = users.get("users", users) if isinstance(users, dict) else {}
        if user_dict and isinstance(user_dict, dict):
            summary_lines.append("")
            summary_lines.append(f"## Users ({len(user_dict)})")
            for username_found in list(user_dict.keys())[:50]:
                summary_lines.append(f"- {username_found}")
            if len(user_dict) > 50:
                summary_lines.append(f"- ... and {len(user_dict) - 50} more")

        # Password policy
        policy = parsed.get("policy") or {}
        if policy and isinstance(policy, dict):
            summary_lines.append("")
            summary_lines.append("## Password policy")
            for k, v in policy.items():
                if isinstance(v, (str, int, bool)):
                    summary_lines.append(f"- {k}: {v}")

        # Errors surfaced by the tool
        errors = parsed.get("errors")
        if errors:
            summary_lines.append("")
            summary_lines.append("## Errors")
            for k, v in (errors.items() if isinstance(errors, dict) else []):
                summary_lines.append(f"- {k}: {v}")
    else:
        # No JSON parsed — fall back to raw stdout
        summary_lines.append("")
        summary_lines.append("## Raw output")
        summary_lines.append(stdout[:8000] if stdout else "(empty)")
        if stderr:
            summary_lines.append("")
            summary_lines.append("## Stderr")
            summary_lines.append(stderr[:2000])

    # ── KB writes ─────────────────────────────────────────────────────
    try:
        kb = for_target(target)

        # Always record the host
        host_fact = HostFact(ip=target)

        # Pull domain/hostname/OS from parsed JSON if available
        if parsed:
            domain_info = parsed.get("domain_info") or parsed.get("smb_domain_info") or {}
            os_info = parsed.get("os_info") or {}
            if isinstance(domain_info, dict):
                d = (domain_info.get("Workgroup")
                     or domain_info.get("Domain")
                     or domain_info.get("workgroup"))
                if d:
                    host_fact = HostFact(ip=target, domain=d)
            if isinstance(os_info, dict):
                os_str = (os_info.get("OS")
                          or os_info.get("os")
                          or os_info.get("Server type"))
                if os_str:
                    host_fact = HostFact(
                        ip=target,
                        os=str(os_str),
                        domain=host_fact.domain,
                    )

        kb.record_host(host_fact)

        # Persist a note with the user-facing summary (so future KB queries
        # can resurrect the enumeration findings without re-running the tool)
        kb.record_note(
            f"[enum4linux-ng/{mode}] {target}\n\n"
            + "\n".join(summary_lines[:80])
        )
    except Exception as e:
        logger.warning("KB write failed in enum4linux_ng: %s", e)
    # ──────────────────────────────────────────────────────────────────

    return format_tool_result("\n".join(summary_lines))


TOOLS.append(enum4linux_ng)
