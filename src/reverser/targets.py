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
