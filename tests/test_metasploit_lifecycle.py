"""Tests for metasploit_start / _stop / _status lifecycle tools.

Subprocess.Popen and _wait_for_rpc_ready are mocked — these tests run without
a real msfrpcd daemon.
"""

import asyncio
import os
import signal
from unittest.mock import patch, MagicMock

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


# ── metasploit_start ────────────────────────────────────────────────


def test_metasploit_start_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.metasploit import metasploit_start
    result = _call(metasploit_start, {"target": "10.10.10.5"})
    assert result.get("is_error") is True


def test_metasploit_start_spawns_daemon_and_writes_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start, _read_pidfile

    mock_proc = MagicMock()
    mock_proc.pid = 99887
    mock_proc.poll.return_value = None  # still running

    with patch("reverser.tools.metasploit.subprocess.Popen",
               return_value=mock_proc) as mock_popen, \
         patch("reverser.tools.metasploit._wait_for_rpc_ready",
               return_value=True), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "started" in text.lower()
    assert "99887" in text
    assert _read_pidfile() == 99887

    # The Popen call should include msfrpcd, -U msf, -P <password>, -a 127.0.0.1,
    # -p 55553, -S (no SSL), -f (foreground)
    args, kwargs = mock_popen.call_args
    cmd = args[0]
    assert cmd[0] == "msfrpcd"
    assert "-U" in cmd
    assert "msf" in cmd
    assert "-P" in cmd
    assert "-a" in cmd
    assert "127.0.0.1" in cmd
    assert "-p" in cmd
    assert "55553" in cmd
    assert "-S" in cmd
    assert "-f" in cmd
    # start_new_session=True for orphan-safe spawn
    assert kwargs.get("start_new_session") is True


def test_metasploit_start_idempotent_when_already_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import (
        metasploit_start, _write_pidfile,
    )
    _write_pidfile(os.getpid())  # use self-pid; will pass _process_alive

    with patch("reverser.tools.metasploit.subprocess.Popen") as mock_popen, \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "already" in text.lower() or "running" in text.lower()
    mock_popen.assert_not_called()


def test_metasploit_start_recovers_stale_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start, _write_pidfile, _read_pidfile
    _write_pidfile(2_000_000_000)  # PID definitely doesn't exist

    mock_proc = MagicMock()
    mock_proc.pid = 33344
    mock_proc.poll.return_value = None
    with patch("reverser.tools.metasploit.subprocess.Popen",
               return_value=mock_proc), \
         patch("reverser.tools.metasploit._wait_for_rpc_ready",
               return_value=True), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "stale" in text.lower() or "recovered" in text.lower() or "started" in text.lower()
    assert _read_pidfile() == 33344


def test_metasploit_start_rpc_ready_timeout(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start, _read_pidfile

    mock_proc = MagicMock()
    mock_proc.pid = 55555
    mock_proc.poll.return_value = None
    with patch("reverser.tools.metasploit.subprocess.Popen",
               return_value=mock_proc), \
         patch("reverser.tools.metasploit._wait_for_rpc_ready",
               return_value=False), \
         patch("os.killpg"):
        result = _call(metasploit_start, {"target": "10.10.10.5"})

    assert result.get("is_error") is True
    assert "rpc" in result["content"][0]["text"].lower() or \
           "timeout" in result["content"][0]["text"].lower()
    # PID file should NOT be left behind on failure
    assert _read_pidfile() is None


def test_metasploit_start_acquires_flock(tmp_targets_dir, monkeypatch):
    """Verify the start lock is acquired and released around Popen."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_start

    order = []
    real_start_lock = None
    from reverser.tools.metasploit import _start_lock as actual_start_lock

    from contextlib import contextmanager
    @contextmanager
    def tracking_lock():
        order.append("lock_acquired")
        with actual_start_lock() as fd:
            yield fd
        order.append("lock_released")

    mock_proc = MagicMock()
    mock_proc.pid = 12121
    mock_proc.poll.return_value = None

    def track_popen(*a, **kw):
        order.append("popen")
        return mock_proc

    with patch("reverser.tools.metasploit._start_lock", tracking_lock), \
         patch("reverser.tools.metasploit.subprocess.Popen",
               side_effect=track_popen), \
         patch("reverser.tools.metasploit._wait_for_rpc_ready", return_value=True), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value = MagicMock()
        _call(metasploit_start, {"target": "10.10.10.5"})

    assert order == ["lock_acquired", "popen", "lock_released"]
