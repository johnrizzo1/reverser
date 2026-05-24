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
