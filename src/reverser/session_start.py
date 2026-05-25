"""Address-resolution rules for `reverser session start`.

Rules (per spec §Programmatic API — resolve_target):
  1. If positional arg matches an existing target name → use it.
  2. Else if it matches the value of an active address on any target → use that target.
  3. Else → create a new target on the fly; arg becomes both name and first address.

Plus: if override_address is supplied and the target exists, add the address
(if new) and promote it to primary.
"""
from __future__ import annotations

import os
from typing import Optional

from reverser.targets import (
    Target,
    _infer_address_kind,
    add_address,
    create_target,
    list_targets,
    load_target,
    set_primary,
)


def _looks_like_file_path(value: str) -> bool:
    """Return True if value points to an existing file on disk."""
    return os.path.isfile(value)


def _infer_target_kind(value: str) -> str:
    """Infer whether a raw arg represents a binary file or a network target."""
    return "binary" if _looks_like_file_path(value) else "network"


def resolve_target(arg: str, *, override_address: Optional[str] = None) -> Target:
    """Resolve `arg` (target name or address value) to a Target.

    Three-rule resolution:
      1. Name match against existing targets.
      2. Active-address-value match across all targets.
      3. Create on the fly (arg is both name and initial address).

    After resolution, if override_address is given, it is added (if not already
    present) and promoted to primary. If it is already the primary, no-op.
    """
    if not arg or not arg.strip():
        raise ValueError("session start requires a target name or address")

    target: Optional[Target] = None

    # Rule 1: exact name match
    try:
        target = load_target(arg)
    except FileNotFoundError:
        pass

    # Rule 2: address-value match across all existing targets
    if target is None:
        for candidate in list_targets():
            for a in candidate.addresses:
                if a.status == "active" and a.value == arg:
                    target = candidate
                    break
            if target is not None:
                break

    # Rule 3: create on the fly
    if target is None:
        kind = _infer_target_kind(arg)
        target = create_target(name=arg, kind=kind, initial_address=arg)

    # --address override: add (if new) and promote to primary
    if override_address is not None:
        existing = next(
            (a for a in target.addresses if a.value == override_address),
            None,
        )
        if existing is None:
            addr_kind = _infer_address_kind(override_address, target.kind)
            target = add_address(target, override_address, kind=addr_kind, make_primary=True)
        elif target.primary_address_id != existing.id:
            target = set_primary(target, existing.id)
        # else: already primary — no-op

    return target
