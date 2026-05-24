"""Tests for src/reverser/targets.py."""
from __future__ import annotations

from reverser.targets import Address


def test_address_round_trip_to_dict():
    addr = Address(
        id="abc123",
        kind="ip",
        value="10.0.0.5",
        status="active",
        added_at="2026-05-24T14:23:00Z",
        label="internal",
    )
    payload = addr.to_dict()
    restored = Address.from_dict(payload)
    assert restored == addr


def test_address_binary_kind_carries_sha256():
    addr = Address(
        id="abc123",
        kind="binary",
        value="/tmp/foo.bin",
        status="active",
        added_at="2026-05-24T14:23:00Z",
        sha256="deadbeef",
    )
    assert addr.to_dict()["sha256"] == "deadbeef"


import pytest
from reverser.targets import Target, Address


def _addr(id="a1", kind="ip", value="10.0.0.1", status="active", **kw):
    return Address(id=id, kind=kind, value=value, status=status,
                   added_at="2026-05-24T00:00:00Z", **kw)


def test_target_primary_must_resolve_to_active_address():
    with pytest.raises(ValueError, match="primary"):
        Target(name="t", kind="network",
               addresses=[_addr(status="retired")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_primary_must_exist_in_addresses():
    with pytest.raises(ValueError, match="primary"):
        Target(name="t", kind="network",
               addresses=[_addr(id="a1")],
               primary_address_id="missing",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_requires_at_least_one_address():
    with pytest.raises(ValueError, match="at least one"):
        Target(name="t", kind="network", addresses=[],
               primary_address_id="",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_network_rejects_binary_address():
    with pytest.raises(ValueError, match="kind"):
        Target(name="t", kind="network",
               addresses=[_addr(kind="binary", value="/tmp/x")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_binary_rejects_network_address():
    with pytest.raises(ValueError, match="kind"):
        Target(name="t", kind="binary",
               addresses=[_addr(kind="ip", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_duplicate_address_value_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Target(name="t", kind="network",
               addresses=[_addr(id="a1", value="10.0.0.1"),
                          _addr(id="a2", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")


def test_target_round_trip_to_dict():
    t = Target(name="dc1", kind="network",
               addresses=[_addr(id="a1", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")
    restored = Target.from_dict(t.to_dict())
    assert restored == t


def test_target_primary_address_property():
    primary = _addr(id="a1", value="10.0.0.1")
    other = _addr(id="a2", value="10.0.0.2")
    t = Target(name="t", kind="network",
               addresses=[primary, other],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")
    assert t.primary_address == primary


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = Target(name="dc1", kind="network",
               addresses=[_addr(id="a1", value="10.0.0.1")],
               primary_address_id="a1",
               created_at="2026-05-24T00:00:00Z",
               updated_at="2026-05-24T00:00:00Z")
    targets.save_target(t)
    loaded = targets.load_target("dc1")
    assert loaded == t


def test_load_unknown_target_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()
    with pytest.raises(FileNotFoundError):
        targets.load_target("nope")


def test_list_targets_returns_all_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    for name in ("alpha", "beta", "gamma"):
        targets.save_target(Target(
            name=name, kind="network",
            addresses=[_addr(id=f"{name}-1", value=f"10.0.0.{ord(name[0])}")],
            primary_address_id=f"{name}-1",
            created_at="2026-05-24T00:00:00Z",
            updated_at="2026-05-24T00:00:00Z",
        ))
    names = sorted(t.name for t in targets.list_targets())
    assert names == ["alpha", "beta", "gamma"]


def test_create_target_with_initial_address(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target(name="dc1", kind="network",
                              initial_address="10.0.0.5")
    assert t.name == "dc1"
    assert t.kind == "network"
    assert len(t.addresses) == 1
    assert t.primary_address.value == "10.0.0.5"
    assert t.primary_address.kind == "ip"


def test_create_target_infers_url_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target(name="webapp", kind="network",
                              initial_address="https://example.com")
    assert t.primary_address.kind == "url"


def test_create_binary_target_computes_sha256(tmp_path, monkeypatch):
    binary = tmp_path / "sample.bin"
    binary.write_bytes(b"hello world")
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "data"))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    t = targets.create_target(name="sample", kind="binary",
                              initial_address=str(binary))
    assert t.primary_address.kind == "binary"
    assert t.primary_address.sha256 is not None
    assert len(t.primary_address.sha256) == 64  # sha256 hex


def test_create_target_rejects_duplicate_name(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser import paths, targets
    paths._reset_caches_for_tests()

    targets.create_target(name="dc1", kind="network", initial_address="10.0.0.5")
    with pytest.raises(ValueError, match="already exists"):
        targets.create_target(name="dc1", kind="network",
                              initial_address="10.0.0.6")
