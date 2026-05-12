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


# ── metasploit_stop ─────────────────────────────────────────────────


def test_metasploit_stop_when_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop
    result = _call(metasploit_stop, {})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "not_running" in text.lower()


def test_metasploit_stop_clears_pidfile_after_sigterm(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop, _write_pidfile, _read_pidfile
    _write_pidfile(7777)

    # Fake: process exits after SIGTERM
    states = {"alive": True}
    def fake_alive(pid):
        return states["alive"]
    def fake_kill(pid, sig):
        if sig == signal.SIGTERM:
            states["alive"] = False

    with patch("reverser.tools.metasploit._process_alive", side_effect=fake_alive), \
         patch("os.kill", side_effect=fake_kill), \
         patch("reverser.tools.metasploit._msf_client") as mock_client:
        mock_client.return_value.sessions.list = {}
        result = _call(metasploit_stop, {})

    assert result.get("is_error") is not True
    assert "stopped" in result["content"][0]["text"].lower()
    assert _read_pidfile() is None


def test_metasploit_stop_warns_when_sessions_open(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop, _write_pidfile
    _write_pidfile(7777)

    states = {"alive": True}
    def fake_alive(pid):
        return states["alive"]
    def fake_kill(pid, sig):
        if sig == signal.SIGTERM:
            states["alive"] = False

    fake_client = MagicMock()
    fake_client.sessions.list = {
        "1": {"type": "meterpreter", "target_host": "10.10.10.5",
              "opened_at": "2026-05-11T12:00:00"},
        "2": {"type": "shell", "target_host": "10.10.10.6",
              "opened_at": "2026-05-11T12:05:00"},
        "3": {"type": "shell", "target_host": "10.10.10.7",
              "opened_at": "2026-05-11T12:10:00"},
    }
    with patch("reverser.tools.metasploit._process_alive", side_effect=fake_alive), \
         patch("os.kill", side_effect=fake_kill), \
         patch("reverser.tools.metasploit._msf_client", return_value=fake_client):
        result = _call(metasploit_stop, {})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    # Per D10: warning surfaced but NOT a refusal
    assert "3" in text  # sessions_lost count
    assert "warning" in text.lower() or "session" in text.lower()
    assert "stopped" in text.lower()


def test_metasploit_stop_force_uses_sigkill_on_timeout(tmp_targets_dir, monkeypatch):
    """With force=True, persistent process is SIGKILLed after 10s SIGTERM wait."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_stop, _write_pidfile
    _write_pidfile(7777)

    signals_received = []
    states = {"alive": True}
    def fake_alive(pid):
        return states["alive"]
    def fake_kill(pid, sig):
        signals_received.append(sig)
        if sig == signal.SIGKILL:
            states["alive"] = False

    # _msf_client throws because the daemon isn't really there
    with patch("reverser.tools.metasploit._process_alive", side_effect=fake_alive), \
         patch("os.kill", side_effect=fake_kill), \
         patch("reverser.tools.metasploit._msf_client",
               side_effect=ConnectionError("refused")), \
         patch("time.sleep"):  # skip the 10s wait
        result = _call(metasploit_stop, {"force": True})

    assert result.get("is_error") is not True
    assert signal.SIGTERM in signals_received
    assert signal.SIGKILL in signals_received


# ── metasploit_status ───────────────────────────────────────────────


def test_metasploit_status_daemon_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status
    result = _call(metasploit_status, {})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "not_running" in text.lower()


def test_metasploit_status_stale_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status, _write_pidfile
    _write_pidfile(2_000_000_000)  # bogus PID
    result = _call(metasploit_status, {})
    text = result["content"][0]["text"]
    # Should still report not-running (stale pidfile detected)
    assert "not running" in text.lower() or "not_running" in text.lower() or "stale" in text.lower()


def test_metasploit_status_daemon_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status, _write_pidfile
    _write_pidfile(os.getpid())  # self-pid for liveness

    fake_client = MagicMock()
    fake_client.core.version = {"version": "6.4.0"}
    fake_client.sessions.list = {
        "1": {"type": "meterpreter", "target_host": "10.10.10.5",
              "opened_at": "2026-05-11T12:00:00"},
    }
    fake_console = MagicMock()
    fake_console.run_with_output.return_value = (
        "Workspaces\n"
        "==========\n"
        "  current  name\n"
        "  -------  ----\n"
        "  *        10.10.10.5\n"
        "           default\n"
    )
    fake_client.consoles.console.return_value = fake_console

    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_status, {})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert str(os.getpid()) in text
    assert "running" in text.lower()
    assert "6.4.0" in text or "version" in text.lower()
    assert "10.10.10.5" in text  # workspace OR session host


def test_metasploit_status_auth_error(tmp_targets_dir, monkeypatch):
    """Daemon process is alive but auth fails — surface the auth error."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_status, _write_pidfile
    _write_pidfile(os.getpid())

    with patch("reverser.tools.metasploit._make_msfrpc_client",
               side_effect=PermissionError("bad password")):
        result = _call(metasploit_status, {})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "auth" in text.lower() or "error" in text.lower()
