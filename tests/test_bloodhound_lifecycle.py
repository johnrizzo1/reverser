"""Tests for bloodhound lifecycle helpers (PID tracking, bolt password, port-collision)."""

import os
import socket
from unittest.mock import patch, MagicMock

from reverser.tools.bloodhound import (
    _neo4j_dir,
    _pid_file,
    _password_file,
    _read_pid,
    _write_pid,
    _clear_pid,
    _ensure_bolt_password,
    _is_port_in_use,
    _BOLT_PORT,
)


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def test_neo4j_dir_under_target(tmp_targets_dir):
    p = _neo4j_dir("10.10.10.5")
    assert p == tmp_targets_dir / "10.10.10.5" / "neo4j"


def test_pid_file_path(tmp_targets_dir):
    assert _pid_file("10.10.10.5") == tmp_targets_dir / "10.10.10.5" / "neo4j" / ".pid"


def test_password_file_path(tmp_targets_dir):
    assert _password_file("10.10.10.5") == tmp_targets_dir / "10.10.10.5" / "neo4j" / "bolt_password"


def test_write_then_read_pid(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 12345)
    assert _read_pid("10.10.10.5") == 12345


def test_read_pid_missing_returns_none(tmp_targets_dir):
    assert _read_pid("10.10.10.5") is None


def test_clear_pid_removes_file(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 99)
    _clear_pid("10.10.10.5")
    assert _read_pid("10.10.10.5") is None


def test_ensure_bolt_password_creates_random(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    pw = _ensure_bolt_password("10.10.10.5")
    assert len(pw) >= 24
    assert _ensure_bolt_password("10.10.10.5") == pw


def test_ensure_bolt_password_persists_to_disk(tmp_targets_dir):
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    pw = _ensure_bolt_password("10.10.10.5")
    on_disk = (tmp_targets_dir / "10.10.10.5" / "neo4j" / "bolt_password").read_text().strip()
    assert on_disk == pw


def test_is_port_in_use_true_when_bound():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        assert _is_port_in_use(port) is True
    finally:
        s.close()


def test_bolt_port_default():
    assert _BOLT_PORT == 7687


def test_bloodhound_start_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_start
    result = _call(bloodhound_start, {"target": "10.10.10.5"})
    assert result.get("is_error") is True
    assert "authoriz" in result["content"][0]["text"].lower()


def test_bloodhound_start_idempotent_returns_existing_pid(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", os.getpid())
    from reverser.tools.bloodhound import bloodhound_start
    result = _call(bloodhound_start, {"target": "10.10.10.5"})
    assert result.get("is_error") is not True
    assert "already running" in result["content"][0]["text"].lower()
    assert str(os.getpid()) in result["content"][0]["text"]


def test_bloodhound_start_clears_stale_pid(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 99999999)
    with patch("reverser.tools.bloodhound._launch_neo4j") as mock_launch, \
         patch("reverser.tools.bloodhound._is_port_in_use", return_value=False):
        mock_launch.return_value = 12345
        from reverser.tools.bloodhound import bloodhound_start, _read_pid
        result = _call(bloodhound_start, {"target": "10.10.10.5"})
        assert result.get("is_error") is not True
        assert _read_pid("10.10.10.5") == 12345


def test_bloodhound_start_refuses_when_other_target_uses_port(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    with patch("reverser.tools.bloodhound._is_port_in_use", return_value=True):
        from reverser.tools.bloodhound import bloodhound_start
        result = _call(bloodhound_start, {"target": "10.10.10.5"})
        assert result.get("is_error") is True
        assert "7687" in result["content"][0]["text"]
        assert "another" in result["content"][0]["text"].lower() or "different" in result["content"][0]["text"].lower()


def test_bloodhound_stop_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_stop
    result = _call(bloodhound_stop, {"target": "10.10.10.5"})
    assert result.get("is_error") is True


def test_bloodhound_stop_no_pid_returns_message(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_stop
    result = _call(bloodhound_stop, {"target": "10.10.10.5"})
    assert result.get("is_error") is not True
    assert "not running" in result["content"][0]["text"].lower()


def test_bloodhound_stop_kills_pid_and_clears_pidfile(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", 12345)
    with patch("reverser.tools.bloodhound._kill_process_group") as mock_kill, \
         patch("reverser.tools.bloodhound._process_alive", return_value=True):
        mock_kill.return_value = True
        from reverser.tools.bloodhound import bloodhound_stop, _read_pid
        result = _call(bloodhound_stop, {"target": "10.10.10.5"})
        assert result.get("is_error") is not True
        mock_kill.assert_called_once_with(12345)
        assert _read_pid("10.10.10.5") is None


def test_bloodhound_status_no_target_lists_known(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    (tmp_targets_dir / "10.10.10.6" / "neo4j").mkdir(parents=True)
    (tmp_targets_dir / "junk").mkdir()
    from reverser.tools.bloodhound import bloodhound_status
    result = _call(bloodhound_status, {})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text
    assert "10.10.10.6" in text
    assert "junk" not in text


def test_bloodhound_status_with_target_no_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    from reverser.tools.bloodhound import bloodhound_status
    result = _call(bloodhound_status, {"target": "10.10.10.5"})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "not running" in text.lower() or "stopped" in text.lower()


def test_bloodhound_status_with_target_running_queries_counts(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    (tmp_targets_dir / "10.10.10.5" / "neo4j").mkdir(parents=True)
    _write_pid("10.10.10.5", os.getpid())
    from reverser.tools.bloodhound import _ensure_bolt_password
    _ensure_bolt_password("10.10.10.5")

    fake_session = MagicMock()
    fake_session.run.return_value = [{"count": 7}]
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    fake_driver.close = MagicMock()
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_status
        result = _call(bloodhound_status, {"target": "10.10.10.5"})
    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "Users" in text
    assert "Computers" in text
    assert "7" in text
