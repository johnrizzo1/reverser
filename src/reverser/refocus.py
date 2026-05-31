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
