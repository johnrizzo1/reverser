"""Tests for bloodhound lifecycle helpers (PID tracking, bolt password, port-collision)."""

import socket

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
