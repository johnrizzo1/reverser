"""Tests for the `session start` address-resolution rules.

Rules (per spec):
1. If positional arg matches an existing target name → use it.
2. Else if it matches the value of an active address on any target → use that target.
3. Else → create a new target on the fly; arg becomes name and first address.

Plus: if --address is also passed and the target exists, add+promote it.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths
    paths._reset_caches_for_tests()
    return tmp_path


def test_resolve_existing_target_by_name(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("dc1")
    assert t.name == "dc1"


def test_resolve_by_address_value_of_existing_target(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("10.0.0.5")
    assert t.name == "dc1"


def test_resolve_creates_new_target_when_unknown(env):
    from reverser.session_start import resolve_target

    t = resolve_target("10.99.99.1")
    assert t.name == "10.99.99.1"
    assert t.kind == "network"
    assert t.primary_address.value == "10.99.99.1"


def test_resolve_creates_binary_target_for_file_path(tmp_path, monkeypatch):
    # Keep the binary file and the targets store in separate directories so
    # target_key(file_path) doesn't collide with the store root.
    binary_dir = tmp_path / "binaries"
    binary_dir.mkdir()
    targets_dir = tmp_path / "store"
    targets_dir.mkdir()

    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    from reverser import paths
    paths._reset_caches_for_tests()

    f = binary_dir / "sample.bin"
    f.write_bytes(b"x")
    from reverser.session_start import resolve_target

    t = resolve_target(str(f))
    assert t.kind == "binary"
    assert t.primary_address.sha256 is not None


def test_address_override_adds_and_promotes_on_existing_target(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("dc1", override_address="10.0.0.6")
    assert t.primary_address.value == "10.0.0.6"
    assert any(a.value == "10.0.0.5" for a in t.addresses)


def test_address_override_idempotent_when_address_already_primary(env):
    from reverser import targets
    from reverser.session_start import resolve_target

    targets.create_target("dc1", "network", "10.0.0.5")
    t = resolve_target("dc1", override_address="10.0.0.5")
    assert t.primary_address.value == "10.0.0.5"
    assert len(t.addresses) == 1


def test_resolve_by_name_takes_priority_over_address_value(env):
    """If a target named '10.0.0.5' exists AND another target has that address value,
    Rule 1 (name match) wins."""
    from reverser import targets
    from reverser.session_start import resolve_target

    t_named = targets.create_target("10.0.0.5", "network", "10.0.0.9")
    targets.create_target("other", "network", "10.0.0.5")
    t = resolve_target("10.0.0.5")
    # Rule 1: name match → the target whose name is "10.0.0.5"
    assert t.name == "10.0.0.5"
    assert t.primary_address.value == "10.0.0.9"


def test_address_override_promotes_existing_non_primary(env):
    """If override_address already exists on the target but is not primary, promote it."""
    from reverser import targets
    from reverser.session_start import resolve_target

    t = targets.create_target("dc1", "network", "10.0.0.5")
    t = targets.add_address(t, "10.0.0.6", kind="ip", make_primary=False)
    assert t.primary_address.value == "10.0.0.5"

    t = resolve_target("dc1", override_address="10.0.0.6")
    assert t.primary_address.value == "10.0.0.6"
    assert len(t.addresses) == 2  # not duplicated
