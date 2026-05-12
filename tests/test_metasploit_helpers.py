"""Tests for metasploit module pure helpers (no subprocess, no daemon)."""

import json
import os

import pytest

from reverser.tools.metasploit import (
    _msf_state_dir,
    _auth_path,
    _pidfile_path,
    _lock_path,
    _read_or_create_auth,
    DEFAULT_RPC_HOST,
    DEFAULT_RPC_PORT,
)


def test_msf_state_dir_under_shared(tmp_targets_dir):
    p = _msf_state_dir()
    assert p == tmp_targets_dir / ".shared" / "msfrpc"


def test_msf_state_dir_created_on_first_access(tmp_targets_dir):
    p = _msf_state_dir()
    assert p.is_dir()
    # 0700 perms (best-effort; checks at least owner-rwx)
    mode = p.stat().st_mode & 0o777
    assert mode & 0o700 == 0o700


def test_auth_path(tmp_targets_dir):
    assert _auth_path() == tmp_targets_dir / ".shared" / "msfrpc" / "auth.json"


def test_pidfile_path(tmp_targets_dir):
    assert _pidfile_path() == tmp_targets_dir / ".shared" / "msfrpc" / "pidfile"


def test_lock_path(tmp_targets_dir):
    assert _lock_path() == tmp_targets_dir / ".shared" / "msfrpc" / "auth.json.lock"


def test_read_or_create_auth_generates_when_missing(tmp_targets_dir):
    auth = _read_or_create_auth()
    assert auth["user"] == "msf"
    assert len(auth["password"]) >= 32
    assert auth["host"] == DEFAULT_RPC_HOST
    assert auth["port"] == DEFAULT_RPC_PORT
    assert auth["ssl"] is False


def test_read_or_create_auth_persists_to_disk_mode_0600(tmp_targets_dir):
    auth = _read_or_create_auth()
    path = _auth_path()
    assert path.is_file()
    mode = path.stat().st_mode & 0o777
    # 0600 — owner read/write only
    assert mode == 0o600
    on_disk = json.loads(path.read_text())
    assert on_disk == auth


def test_read_or_create_auth_roundtrips(tmp_targets_dir):
    first = _read_or_create_auth()
    second = _read_or_create_auth()
    assert first == second


def test_default_rpc_constants():
    assert DEFAULT_RPC_HOST == "127.0.0.1"
    assert DEFAULT_RPC_PORT == 55553
