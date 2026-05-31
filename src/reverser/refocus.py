"""Refocus a target onto a new IP: promote a new address, remap KB rows, and
optionally update /etc/hosts. (refocus_target/RefocusResult are added later.)"""

from __future__ import annotations


def rewrite_hosts_entry(path: str, hostname: str, old_ip: str | None, new_ip: str) -> bool:
    """Point `hostname` at `new_ip` in a hosts file. Returns True if changed.

    Rewrites any line whose host column list contains `hostname` to use `new_ip`;
    if no such line exists, appends `new_ip hostname`. Comments/blank lines are
    preserved. Pure file operation — the caller decides whether to run under sudo.
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


# ---------------------------------------------------------------------------
# refocus_target — promote a new IP, remap KB rows, optionally patch /etc/hosts
# ---------------------------------------------------------------------------

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
    from .targets import load_target, add_address, set_primary
    from .kb.store import KB
    from .kb.scope import load_scope

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

    scope_warning = None
    scope = load_scope(target.name)
    if scope is not None and not scope.is_target_in_scope(new_ip):
        msg = f"{new_ip} is out of scope per scope.toml"
        if not force_scope:
            raise RefocusScopeError(msg)
        scope_warning = msg + " (applied with force_scope)"

    existing = next((a for a in target.addresses if a.value == new_ip), None)
    if existing is not None:
        target = set_primary(target, existing.id)
        new_address_id = existing.id
    else:
        target = add_address(target, new_ip, "ip", label="refocus", make_primary=True)
        new_address_id = target.primary_address_id

    rows = KB(target.name).remap_address(old_ip, new_ip)

    hostname_updated = False
    if update_etc_hosts and hostname:
        try:
            hostname_updated = rewrite_hosts_entry(hosts_path, hostname, old_ip, new_ip)
        except OSError:
            hostname_updated = False

    return RefocusResult(
        target=target.name, old_ip=old_ip, new_ip=new_ip,
        rows_remapped=rows, hostname_updated=hostname_updated,
        scope_warning=scope_warning, new_address_id=new_address_id,
    )
