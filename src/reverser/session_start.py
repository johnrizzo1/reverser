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
from urllib.parse import urlparse

from reverser.kb import ArtifactFact, HostFact, for_target
from reverser.sessions import target_key
from reverser.targets import (
    Address,
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


def _kb_ids_for(target: Target) -> set[str]:
    ids = {target_key(target.primary_address.value)}
    return {i for i in ids if i}


def _host_value_for_address(address: Address) -> str | None:
    if address.kind == "ip":
        return address.value
    if address.kind == "url":
        parsed = urlparse(address.value)
        return parsed.hostname or address.value
    return None


def _seed_target_kb(target: Target) -> None:
    """Seed durable KB context from the target model.

    This makes the user-supplied engagement target visible to the agent before
    any scanner has run. Writes are idempotent for hosts/artifacts and note
    creation is guarded to avoid repeated session-start spam.
    """
    primary = target.primary_address
    host_value = _host_value_for_address(primary)
    note = (
        "[session-start] Initial engagement target: "
        f"name={target.name}; kind={target.kind}; "
        f"primary_address={primary.value}; address_kind={primary.kind}"
    )

    for kb_id in _kb_ids_for(target):
        kb = for_target(kb_id)
        if host_value is not None:
            kb.record_host(HostFact(ip=host_value))
        elif primary.kind == "binary":
            if not any(a.path == primary.value for a in kb.get_artifacts()):
                kb.record_artifact(
                    ArtifactFact(
                        kind="target_binary",
                        path=primary.value,
                        sha256=primary.sha256,
                        source_tool="session_start",
                    )
                )
        if note not in kb.get_notes():
            kb.record_note(note)


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
        initial_address = override_address if override_address is not None else arg
        kind = _infer_target_kind(initial_address)
        target = create_target(name=arg, kind=kind, initial_address=initial_address)

    # --address override: add (if new) and promote to primary
    if override_address is not None and target.primary_address.value != override_address:
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

    _seed_target_kb(target)
    return target
