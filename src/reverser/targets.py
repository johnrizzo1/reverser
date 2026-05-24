"""Target and Address model: per-engagement logical assets with mutable addresses.

A Target is a named logical asset (an AD DC, a web app, a binary) that owns
the per-target KB, scope, and sessions. An Address is one IP/URL/file path
by which that target is reached; addresses are mutable history with one
marked primary.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

AddressKind = Literal["ip", "url", "binary"]
AddressStatus = Literal["active", "retired"]
TargetKind = Literal["network", "binary"]

_NETWORK_KINDS: frozenset[str] = frozenset({"ip", "url"})
_BINARY_KINDS: frozenset[str] = frozenset({"binary"})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Address:
    id: str
    kind: AddressKind
    value: str
    status: AddressStatus
    added_at: str
    sha256: Optional[str] = None
    retired_at: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, payload: dict) -> "Address":
        return cls(
            id=payload["id"],
            kind=payload["kind"],
            value=payload["value"],
            status=payload["status"],
            added_at=payload["added_at"],
            sha256=payload.get("sha256"),
            retired_at=payload.get("retired_at"),
            label=payload.get("label"),
        )


@dataclass
class Target:
    name: str
    kind: TargetKind
    addresses: list[Address]
    primary_address_id: str
    created_at: str
    updated_at: str
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        self._validate()

    def _allowed_address_kinds(self) -> frozenset[str]:
        return _NETWORK_KINDS if self.kind == "network" else _BINARY_KINDS

    def _validate(self) -> None:
        if not self.addresses:
            raise ValueError(f"Target {self.name!r} must have at least one address")
        allowed = self._allowed_address_kinds()
        seen_values: set[str] = set()
        seen_ids: dict[str, Address] = {}
        for a in self.addresses:
            if a.kind not in allowed:
                raise ValueError(
                    f"Target {self.name!r} kind={self.kind!r} rejects address "
                    f"kind={a.kind!r} (allowed: {sorted(allowed)})"
                )
            if a.value in seen_values:
                raise ValueError(
                    f"Target {self.name!r} has duplicate address value {a.value!r}"
                )
            seen_values.add(a.value)
            seen_ids[a.id] = a
        primary = seen_ids.get(self.primary_address_id)
        if primary is None:
            raise ValueError(
                f"Target {self.name!r} primary_address_id={self.primary_address_id!r} "
                "does not match any address"
            )
        if primary.status != "active":
            raise ValueError(
                f"Target {self.name!r} primary address must be active "
                f"(got status={primary.status!r})"
            )

    @property
    def primary_address(self) -> Address:
        for a in self.addresses:
            if a.id == self.primary_address_id:
                return a
        raise ValueError(f"primary address {self.primary_address_id!r} not found")

    def get_address(self, address_id: str) -> Address:
        for a in self.addresses:
            if a.id == address_id:
                return a
        raise KeyError(address_id)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "addresses": [a.to_dict() for a in self.addresses],
            "primary_address_id": self.primary_address_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Target":
        return cls(
            name=payload["name"],
            kind=payload["kind"],
            addresses=[Address.from_dict(a) for a in payload["addresses"]],
            primary_address_id=payload["primary_address_id"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            notes=payload.get("notes"),
        )


from reverser.paths import targets_root
from reverser.sessions import target_key  # reuse existing slug logic

_TARGET_FILE = "target.json"


def _target_dir(name: str) -> Path:
    return targets_root() / target_key(name)


def load_target(name: str) -> Target:
    path = _target_dir(name) / _TARGET_FILE
    if not path.exists():
        raise FileNotFoundError(f"No target named {name!r} at {path}")
    with path.open("r", encoding="utf-8") as f:
        return Target.from_dict(json.load(f))


def save_target(target: Target) -> None:
    target._validate()  # final defensive check
    directory = _target_dir(target.name)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _TARGET_FILE
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(target.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def list_targets() -> list[Target]:
    root = targets_root()
    if not root.exists():
        return []
    out: list[Target] = []
    for entry in sorted(root.iterdir()):
        candidate = entry / _TARGET_FILE
        if candidate.is_file():
            with candidate.open("r", encoding="utf-8") as f:
                out.append(Target.from_dict(json.load(f)))
    return out


def _infer_address_kind(value: str, target_kind: TargetKind) -> AddressKind:
    if target_kind == "binary":
        return "binary"
    if value.startswith(("http://", "https://")):
        return "url"
    return "ip"


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_address(value: str, kind: AddressKind, label: Optional[str] = None) -> Address:
    sha = None
    if kind == "binary":
        sha = _sha256_of_file(value)
    return Address(
        id=uuid.uuid4().hex,
        kind=kind,
        value=value,
        status="active",
        added_at=_now_iso(),
        sha256=sha,
        label=label,
    )


def create_target(
    name: str,
    kind: TargetKind,
    initial_address: str,
    *,
    label: Optional[str] = None,
) -> Target:
    """Create and persist a new target with one initial primary address."""
    directory = _target_dir(name)
    if (directory / _TARGET_FILE).exists():
        raise ValueError(f"Target {name!r} already exists")
    addr_kind = _infer_address_kind(initial_address, kind)
    address = _new_address(initial_address, addr_kind, label=label)
    now = _now_iso()
    target = Target(
        name=name,
        kind=kind,
        addresses=[address],
        primary_address_id=address.id,
        created_at=now,
        updated_at=now,
    )
    save_target(target)
    return target


def add_address(
    target: Target,
    value: str,
    kind: AddressKind,
    *,
    label: Optional[str] = None,
    make_primary: bool = False,
) -> Target:
    """Add a new address. Returns the updated target (also persisted)."""
    if any(a.value == value for a in target.addresses):
        raise ValueError(f"Target {target.name!r} already has address {value!r} (duplicate)")
    allowed = target._allowed_address_kinds()
    if kind not in allowed:
        raise ValueError(
            f"Target {target.name!r} kind={target.kind!r} rejects address kind={kind!r}"
        )
    address = _new_address(value, kind, label=label)
    updated = dataclasses.replace(
        target,
        addresses=[*target.addresses, address],
        primary_address_id=address.id if make_primary else target.primary_address_id,
        updated_at=_now_iso(),
    )
    save_target(updated)
    return updated


def set_primary(target: Target, address_id: str) -> Target:
    """Promote an existing active address to primary."""
    addr = target.get_address(address_id)
    if addr.status != "active":
        raise ValueError(
            f"Cannot set primary to retired address {address_id!r}; re-add it first"
        )
    updated = dataclasses.replace(
        target,
        primary_address_id=address_id,
        updated_at=_now_iso(),
    )
    save_target(updated)
    return updated


def retire_address(target: Target, address_id: str) -> Target:
    """Mark an address retired. Refuses to retire the primary or the last active."""
    addr = target.get_address(address_id)
    actives = [a for a in target.addresses if a.status == "active"]
    if len(actives) <= 1:
        raise ValueError(
            f"Cannot retire {address_id!r}: it is the last active address on {target.name!r}"
        )
    if address_id == target.primary_address_id:
        raise ValueError(
            f"Cannot retire primary address {address_id!r}; promote another active address first"
        )
    new_addresses = []
    for a in target.addresses:
        if a.id == address_id:
            a = dataclasses.replace(a, status="retired", retired_at=_now_iso())
        new_addresses.append(a)
    updated = dataclasses.replace(target, addresses=new_addresses, updated_at=_now_iso())
    save_target(updated)
    return updated


def _has_active_sessions(name: str) -> list[str]:
    """Return ids (filenames) of any session snapshots in lifecycle state 'active'."""
    sessions_dir = _target_dir(name) / "sessions"
    if not sessions_dir.exists():
        return []
    active: list[str] = []
    for snapshot_path in sessions_dir.glob("*.json"):
        try:
            with snapshot_path.open("r", encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if snap.get("state") == "active":
            active.append(snapshot_path.stem)
    return active


def rename_target(old_name: str, new_name: str) -> Target:
    """Rename a target by moving its on-disk directory atomically.

    Refuses if any session on the target is in lifecycle state 'active'.
    """
    old_dir = _target_dir(old_name)
    new_dir = _target_dir(new_name)
    if not (old_dir / _TARGET_FILE).exists():
        raise FileNotFoundError(f"No target named {old_name!r}")
    if new_dir.exists():
        raise ValueError(f"Target {new_name!r} already exists at {new_dir}")
    active = _has_active_sessions(old_name)
    if active:
        raise ValueError(
            f"Cannot rename {old_name!r}: active session(s) {active}; stop them first"
        )
    os.replace(old_dir, new_dir)
    # Update name field inside target.json.
    t = load_target(new_name)
    updated = dataclasses.replace(t, name=new_name, updated_at=_now_iso())
    save_target(updated)
    return updated
