"""Optional per-target scope.toml loader + enforcement helpers.

If `targets/<target>/scope.toml` exists, the active tools should call
`load_scope(target)` and consult the returned `Scope` object before doing
anything that touches the network. If no scope.toml exists, `load_scope`
returns None and no enforcement is performed (legacy behavior).
"""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass, field
from typing import Optional

from .store import normalize_target
from reverser.paths import targets_root


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
        """Return True if `ip` is in scope (CIDR match) and not on the exclusion list."""
        if ip in self.out_of_scope_ips:
            return False
        if not self.in_scope_cidrs:
            return True
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            return True
        for cidr in self.in_scope_cidrs:
            try:
                if ip_obj in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def assert_in_scope(self, ip: str) -> None:
        if not self.is_target_in_scope(ip):
            raise ScopeError(
                f"target {ip!r} is out of scope per scope.toml "
                f"(in_scope_cidrs={self.in_scope_cidrs}, "
                f"out_of_scope_ips={self.out_of_scope_ips})"
            )

    def assert_spray_allowed(self) -> None:
        if self.no_account_lockout:
            raise ScopeError(
                "credential spraying is forbidden by scope.toml "
                "(no_account_lockout = true). Edit targets/<target>/scope.toml "
                "to enable, or use a single-attempt check_auth instead."
            )

    def assert_dos_allowed(self) -> None:
        if self.no_dos:
            raise ScopeError(
                "this action is forbidden by scope.toml (no_dos = true) - "
                "denial-of-service-prone operations are out of scope for this engagement."
            )


def load_scope(target: str) -> Optional[Scope]:
    """Load `targets/<target>/scope.toml`. Return None if the file does not exist."""
    target_id = normalize_target(target)
    path = targets_root() / target_id / "scope.toml"
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
