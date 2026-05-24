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
