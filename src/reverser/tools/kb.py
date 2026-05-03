"""Read-side and editorial tools the LLM uses to inspect/annotate the KB."""

from __future__ import annotations

from collections import Counter

from claude_agent_sdk import tool

from ..kb import (
    AuthorizationError,
    FindingFact,
    for_target,
    list_targets,
    require_pentest_auth,
)
from ._common import format_error, format_tool_result


def _resolve_target(target: str | None):
    """Return a normalized target name or an MCP error result.

    If `target` is provided, return it. If `target` is None and exactly one
    target subdir exists, return it. Otherwise return (None, error_dict).
    """
    if target:
        return target
    candidates = list_targets()
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None, format_error(
            "No targets found in REVERSER_TARGETS_DIR. "
            "Provide a target argument explicitly."
        )
    return None, format_error(
        "Multiple targets present — pass `target` explicitly. "
        "Available: " + ", ".join(candidates)
    )


def _check_auth() -> dict | None:
    try:
        require_pentest_auth()
        return None
    except AuthorizationError as e:
        return format_error(str(e))


@tool(
    "kb_show",
    "Single-screen overview of the per-target knowledge base: hosts (count and "
    "OS breakdown), top 10 ports, valid credentials count + most recent, finding "
    "count by severity. If `target` is omitted and exactly one target has been "
    "started, defaults to it; otherwise errors with the available list.",
    {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Normalized target identifier (IP/hostname/CIDR). Optional.",
                "default": "",
            },
        },
    },
)
async def kb_show(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    target_arg = args.get("target", "") or None
    resolved = _resolve_target(target_arg)
    if isinstance(resolved, tuple):
        return resolved[1]
    target = resolved

    kb = for_target(target)
    hosts = kb.get_hosts()
    services = kb.get_services()
    creds = kb.get_credentials()
    valid_creds = [c for c in creds if c.status == "valid"]
    findings = kb.get_findings()

    port_counter = Counter(s.port for s in services)
    top_ports = port_counter.most_common(10)
    os_counter = Counter((h.os or "unknown") for h in hosts)
    sev_counter = Counter(f.severity for f in findings)

    lines = [
        f"# KB summary — {target}",
        "",
        f"Hosts: {len(hosts)}",
    ]
    for os_name, n in os_counter.most_common():
        lines.append(f"  - {os_name}: {n}")
    lines.append("")
    lines.append(f"Services: {len(services)}")
    if top_ports:
        lines.append("Top ports:")
        for port, count in top_ports:
            lines.append(f"  - {port}: {count}")
    lines.append("")
    lines.append(f"Credentials: {len(creds)} total, {len(valid_creds)} valid")
    if valid_creds:
        most_recent = valid_creds[-1]
        lines.append(
            f"  - Most recent valid: {most_recent.username}"
            f" (source: {most_recent.source_tool or '?'})"
        )
    lines.append("")
    lines.append(f"Findings: {len(findings)}")
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev_counter.get(sev):
            lines.append(f"  - {sev}: {sev_counter[sev]}")

    return format_tool_result("\n".join(lines))


TOOLS = [kb_show]


@tool(
    "kb_list_hosts",
    "List every host in the KB for `target`: ip, hostname, OS, domain, "
    "is_dc, smb_signing.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
        },
        "required": ["target"],
    },
)
async def kb_list_hosts(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    kb = for_target(target)
    hosts = kb.get_hosts()
    if not hosts:
        return format_tool_result(f"No hosts recorded for {target} (0 rows)")
    lines = [f"# Hosts for {target} ({len(hosts)} rows)", ""]
    lines.append(f"{'IP':<18}{'HOSTNAME':<28}{'OS':<32}{'DOMAIN':<20}{'DC':<5}SIGNING")
    lines.append("-" * 110)
    for h in hosts:
        lines.append(
            f"{h.ip:<18}{(h.hostname or '-'):<28}{(h.os or '-')[:31]:<32}"
            f"{(h.domain or '-'):<20}{('yes' if h.is_dc else 'no'):<5}{h.smb_signing or '-'}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_hosts)


@tool(
    "kb_list_services",
    "List every service in the KB for `target`. Optional `host` and `port` filters.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "host": {"type": "string", "description": "Filter by host IP.", "default": ""},
            "port": {"type": "integer", "description": "Filter by port.", "default": 0},
        },
        "required": ["target"],
    },
)
async def kb_list_services(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    host = args.get("host", "") or None
    port = args.get("port", 0) or None
    kb = for_target(target)
    services = kb.get_services(host_ip=host, port=port)
    if not services:
        return format_tool_result(f"No services match for {target} (0 rows)")
    lines = [f"# Services for {target} ({len(services)} rows)", ""]
    lines.append(f"{'HOST':<18}{'PORT':<6}{'PROTO':<6}{'SERVICE':<20}{'VERSION':<40}SOURCE")
    lines.append("-" * 100)
    for s in services:
        lines.append(
            f"{s.host_ip:<18}{s.port:<6}{s.proto:<6}{(s.service or '-'):<20}"
            f"{(s.version or '-')[:39]:<40}{s.scan_source or '-'}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_services)
