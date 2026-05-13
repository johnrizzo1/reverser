"""Tests for the per-target connection-failure circuit breaker."""

import pytest


# ── Counter state ────────────────────────────────────────────────────


def test_counter_starts_at_zero():
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    assert _conn_breaker.is_tripped("10.10.10.5") is False
    summary = _conn_breaker.failure_summary("10.10.10.5")
    assert summary["count"] == 0


def test_record_failure_increments():
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    _conn_breaker.record_failure("10.10.10.5")
    assert _conn_breaker.failure_summary("10.10.10.5")["count"] == 1
    _conn_breaker.record_failure("10.10.10.5")
    assert _conn_breaker.failure_summary("10.10.10.5")["count"] == 2


def test_below_threshold_not_tripped():
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    _conn_breaker.record_failure("10.10.10.5")
    _conn_breaker.record_failure("10.10.10.5")
    # 2 failures, threshold is 3
    assert _conn_breaker.is_tripped("10.10.10.5") is False


def test_is_tripped_at_threshold():
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    for _ in range(3):
        _conn_breaker.record_failure("10.10.10.5")
    assert _conn_breaker.is_tripped("10.10.10.5") is True


def test_reset_for_target_clears_counter():
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    for _ in range(3):
        _conn_breaker.record_failure("10.10.10.5")
    assert _conn_breaker.is_tripped("10.10.10.5") is True
    _conn_breaker.reset_for_target("10.10.10.5")
    assert _conn_breaker.is_tripped("10.10.10.5") is False


def test_reset_all_clears_everything():
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    for _ in range(3):
        _conn_breaker.record_failure("10.10.10.5")
        _conn_breaker.record_failure("10.10.10.6")
    _conn_breaker.reset_all()
    assert _conn_breaker.is_tripped("10.10.10.5") is False
    assert _conn_breaker.is_tripped("10.10.10.6") is False


def test_per_target_isolation():
    """Tripping target A should NOT affect target B."""
    from reverser.tools import _conn_breaker
    _conn_breaker.reset_all()
    for _ in range(3):
        _conn_breaker.record_failure("10.10.10.5")
    assert _conn_breaker.is_tripped("10.10.10.5") is True
    assert _conn_breaker.is_tripped("10.10.10.6") is False


# ── looks_like_conn_error classifier ─────────────────────────────────


def test_looks_like_conn_error_connection_refused():
    from reverser.tools._conn_breaker import looks_like_conn_error
    assert looks_like_conn_error("curl: (7) Failed to connect: Connection refused") is True


def test_looks_like_conn_error_timeout():
    from reverser.tools._conn_breaker import looks_like_conn_error
    assert looks_like_conn_error("Connection timed out after 30000 ms") is True


def test_looks_like_conn_error_no_route_to_host():
    from reverser.tools._conn_breaker import looks_like_conn_error
    assert looks_like_conn_error("nmap: No route to host") is True


def test_looks_like_conn_error_rejects_http_4xx_5xx():
    """HTTP errors mean the target IS up — should NOT trip the breaker."""
    from reverser.tools._conn_breaker import looks_like_conn_error
    assert looks_like_conn_error("HTTP/1.1 500 Internal Server Error") is False
    assert looks_like_conn_error("404 Not Found") is False


def test_looks_like_conn_error_rejects_tls_error():
    """TLS handshake errors mean target is up but TLS misconfigured."""
    from reverser.tools._conn_breaker import looks_like_conn_error
    assert looks_like_conn_error("SSL routines:tls_process_server_certificate") is False


def test_looks_like_conn_error_empty_string():
    from reverser.tools._conn_breaker import looks_like_conn_error
    assert looks_like_conn_error("") is False
    assert looks_like_conn_error(None) is False


# ── run_cmd integration ──────────────────────────────────────────────


def test_run_cmd_bails_when_tripped(monkeypatch):
    """If the breaker is tripped, run_cmd returns an error without running."""
    from reverser.tools import _conn_breaker
    from reverser.tools._common import run_cmd

    _conn_breaker.reset_all()
    for _ in range(3):
        _conn_breaker.record_failure("10.10.10.5")
    assert _conn_breaker.is_tripped("10.10.10.5") is True

    called = []
    def fake_subprocess_run(*a, **kw):
        called.append(True)
        raise AssertionError("subprocess should not have been called")
    monkeypatch.setattr("subprocess.run", fake_subprocess_run)

    result = run_cmd(["echo", "test"], target="10.10.10.5")
    assert result.get("is_error") is True
    assert "circuit breaker" in result["stderr"].lower()
    assert called == []  # subprocess.run was NOT called

    _conn_breaker.reset_all()


def test_run_cmd_records_failure_on_conn_error_output(monkeypatch):
    """When a subprocess fails with conn-error output, the counter increments."""
    from reverser.tools import _conn_breaker
    from reverser.tools._common import run_cmd

    _conn_breaker.reset_all()

    class FakeProc:
        stdout = ""
        stderr = "curl: (7) Failed to connect to 10.10.10.5: Connection refused"
        returncode = 7

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())

    result = run_cmd(["curl", "http://10.10.10.5"], target="10.10.10.5")
    assert "Connection refused" in result["stderr"]
    assert _conn_breaker.failure_summary("10.10.10.5")["count"] == 1

    _conn_breaker.reset_all()


def test_run_cmd_no_target_no_breaker_interaction(monkeypatch):
    """If target=None (default), the breaker is never touched."""
    from reverser.tools import _conn_breaker
    from reverser.tools._common import run_cmd

    _conn_breaker.reset_all()

    class FakeProc:
        stdout = ""
        stderr = "Connection refused"
        returncode = 7

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())

    # No target= passed → breaker counter stays at 0 even with conn-error output
    run_cmd(["curl", "http://anywhere"])
    assert _conn_breaker.failure_summary("anywhere")["count"] == 0
