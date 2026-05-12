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


# ── Task 6: pidfile + flock helpers ─────────────────────────────────

import time
from contextlib import contextmanager


def test_pidfile_read_when_missing(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile
    assert _read_pidfile() is None


def test_pidfile_write_then_read(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile, _write_pidfile
    _write_pidfile(12345)
    assert _read_pidfile() == 12345


def test_pidfile_remove(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile, _write_pidfile, _remove_pidfile
    _write_pidfile(99)
    _remove_pidfile()
    assert _read_pidfile() is None


def test_pidfile_remove_when_missing_is_noop(tmp_targets_dir):
    from reverser.tools.metasploit import _remove_pidfile
    # Should not raise
    _remove_pidfile()


def test_pidfile_corrupted_returns_none(tmp_targets_dir):
    from reverser.tools.metasploit import _read_pidfile, _pidfile_path
    _pidfile_path().write_text("not-a-number")
    assert _read_pidfile() is None


def test_process_alive_self_returns_true():
    from reverser.tools.metasploit import _process_alive
    assert _process_alive(os.getpid()) is True


def test_process_alive_huge_pid_returns_false():
    from reverser.tools.metasploit import _process_alive
    # PID 2_000_000_000 almost certainly doesn't exist
    assert _process_alive(2_000_000_000) is False


def test_start_lock_acquires_and_releases(tmp_targets_dir):
    from reverser.tools.metasploit import _start_lock
    with _start_lock() as lock_fd:
        assert lock_fd is not None
    # Should be able to re-acquire (released on context exit)
    with _start_lock() as lock_fd2:
        assert lock_fd2 is not None


def test_start_lock_creates_lock_file(tmp_targets_dir):
    from reverser.tools.metasploit import _start_lock, _lock_path
    with _start_lock():
        assert _lock_path().is_file()


# ── Task 7: RPC-ready poll + MsfRpcClient wrapper ───────────────────

from unittest.mock import patch, MagicMock


def test_wait_for_rpc_ready_succeeds_quickly(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import _wait_for_rpc_ready
    auth = {"user": "msf", "password": "x", "host": "127.0.0.1",
            "port": 55553, "ssl": False}
    mock_client = MagicMock()
    mock_client.core.version = {"version": "6.4.0"}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=mock_client):
        assert _wait_for_rpc_ready(auth, timeout_seconds=2) is True


def test_wait_for_rpc_ready_times_out(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import _wait_for_rpc_ready
    auth = {"user": "msf", "password": "x", "host": "127.0.0.1",
            "port": 55553, "ssl": False}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               side_effect=ConnectionError("refused")):
        # Use a short timeout so the test is fast
        start = time.time()
        ok = _wait_for_rpc_ready(auth, timeout_seconds=1)
        elapsed = time.time() - start
    assert ok is False
    assert elapsed >= 1.0  # waited at least the timeout


def test_msf_client_creates_workspace_for_target(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import _msf_client
    mock_client = MagicMock()
    mock_console = MagicMock()
    mock_client.consoles.console.return_value = mock_console
    # Existing workspaces don't include our target; client should add it
    mock_console.run_with_output.return_value = "Workspaces\n* default\n"
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=mock_client):
        with patch("reverser.tools.metasploit._read_or_create_auth",
                   return_value={"user": "msf", "password": "x",
                                 "host": "127.0.0.1", "port": 55553, "ssl": False}):
            client = _msf_client("10.10.10.5")
    assert client is mock_client
    # Should have asked the console to add + use the workspace
    calls = [str(c) for c in mock_console.run_with_output.call_args_list]
    joined = " ".join(calls)
    assert "workspace -a" in joined
    assert "10.10.10.5" in joined


def test_workspace_name_for_target():
    from reverser.tools.metasploit import _workspace_name_for
    # Plain IPs are unchanged
    assert _workspace_name_for("10.10.10.5") == "10.10.10.5"
    # Workspace names are case-sensitive in MSF; we keep them as-is after
    # normalize_target (which lowercases). Hostnames OK.
    assert _workspace_name_for("DC01.CORP.LOCAL") == "dc01.corp.local"
