"""Tests for metasploit_search / _run / _session — the operational tools.

Uses mocked pymetasploit3.MsfRpcClient throughout. No daemon required.
"""

import asyncio
import os
from unittest.mock import patch, MagicMock

import pytest


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


# ── metasploit_search ───────────────────────────────────────────────


_FAKE_MODULE_RESULTS = [
    {
        "fullname": "exploit/multi/http/proftpd_modcopy_exec",
        "type": "exploit",
        "platform": "linux",
        "rank": "great",
        "disclosure_date": "2015-04-07",
        "description": "ProFTPd 1.3.5 mod_copy RCE",
        "ref": ["CVE-2015-3306"],
    },
    {
        "fullname": "auxiliary/scanner/ftp/ftp_login",
        "type": "auxiliary",
        "platform": "",
        "rank": "normal",
        "disclosure_date": "",
        "description": "FTP login brute-force",
        "ref": [],
    },
]


def test_metasploit_search_returns_modules(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())  # daemon alive

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd"})

    assert result.get("is_error") is not True
    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text
    assert "great" in text
    fake_client.modules.search.assert_called_with("proftpd")


def test_metasploit_search_filters_by_type(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd", "type": "exploit"})

    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text
    # auxiliary should be filtered out
    assert "ftp_login" not in text


def test_metasploit_search_filters_by_platform(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd", "platform": "linux"})

    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text


def test_metasploit_search_filters_by_rank(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.modules.search.return_value = _FAKE_MODULE_RESULTS
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_search, {"query": "proftpd", "rank": "great"})

    text = result["content"][0]["text"]
    assert "proftpd_modcopy_exec" in text
    assert "ftp_login" not in text  # rank=normal filtered out


def test_metasploit_search_daemon_not_running(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_search
    # No pidfile
    result = _call(metasploit_search, {"query": "proftpd"})
    assert result.get("is_error") is True
    assert "metasploit_start" in result["content"][0]["text"]


# ── metasploit_run: decision matrix ─────────────────────────────────


def _fake_module(check_code: str | None, exploit_returns_session: bool):
    """Build a fake MSF module object.

    check_code: one of None (no_check_method), "vulnerable", "safe",
                "unknown", "detected", "error"
    exploit_returns_session: if True, .execute() returns a job_id that
                             eventually yields a session in sessions.list
    """
    mod = MagicMock()
    mod._opts = {}
    mod.__setitem__ = lambda self, k, v: mod._opts.__setitem__(k, v)
    mod.__getitem__ = lambda self, k: mod._opts[k]

    if check_code is None:
        # No check method — pymetasploit3 raises here in real use; we model
        # by raising NotImplementedError when .check_exploit() is called.
        mod.check_exploit.side_effect = NotImplementedError("no check method")
    else:
        mod.check_exploit.return_value = {"code": check_code,
                                          "message": f"check returned {check_code}"}

    if exploit_returns_session:
        mod.execute.return_value = {"job_id": 1, "uuid": "abcd"}
    else:
        mod.execute.return_value = {"job_id": None}
    return mod


def _client_with_module(mod, *, sessions_after_exploit: dict | None = None):
    client = MagicMock()
    client.modules.use.return_value = mod
    client.sessions.list = sessions_after_exploit or {}
    return client


def _setup_run_test(monkeypatch, *, check_code, exploit_yields_session=False,
                    sessions=None):
    """Common harness for metasploit_run tests. Returns (client, mod, _call_target)."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    mod = _fake_module(check_code, exploit_yields_session)
    client = _client_with_module(mod, sessions_after_exploit=sessions)
    return client, mod


def test_run_check_vulnerable_runs_exploit(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="vulnerable",
                                   exploit_yields_session=True,
                                   sessions={"1": {"type": "meterpreter",
                                                   "target_host": "10.10.10.5"}})
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5", "RPORT": 80},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "vulnerable" in text.lower()
    assert "session" in text.lower()
    mod.execute.assert_called()  # exploit fired


def test_run_check_safe_skips_exploit(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="safe")
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "safe" in text.lower()
    assert "skip" in text.lower() or "not" in text.lower()
    mod.execute.assert_not_called()


def test_run_check_unknown_skips_exploit(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="unknown")
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "unknown" in text.lower()
    mod.execute.assert_not_called()


def test_run_no_check_method_skips_exploit_by_default(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code=None)
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })
    text = result["content"][0]["text"]
    assert "no_check_method" in text.lower() or "no check method" in text.lower()
    mod.execute.assert_not_called()


def test_run_force_overrides_safe_check(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code="safe",
                                   exploit_yields_session=False)
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
            "force": True,
        })
    text = result["content"][0]["text"]
    assert "safe" in text.lower()
    mod.execute.assert_called()  # force bypasses skip


def test_run_force_overrides_no_check_method(tmp_targets_dir, monkeypatch):
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())
    client, mod = _setup_run_test(monkeypatch, check_code=None)
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
            "force": True,
        })
    mod.execute.assert_called()


# ── metasploit_run: scope + auto-finding ────────────────────────────


def test_run_scope_violation_before_check(tmp_targets_dir, monkeypatch):
    """If target is out of scope, scope_toml must abort BEFORE check fires."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    _write_pidfile(os.getpid())

    # Write a scope.toml that excludes 10.10.10.5
    target_dir = tmp_targets_dir / "10.10.10.5"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "scope.toml").write_text(
        '[scope]\nin_scope_cidrs = ["192.168.0.0/24"]\n'
    )

    mod = MagicMock()
    mod.check_exploit.return_value = {"code": "vulnerable", "message": ""}
    fake_client = MagicMock()
    fake_client.modules.use.return_value = mod

    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })

    assert result.get("is_error") is True
    assert "scope" in result["content"][0]["text"].lower()
    # KEY: check_exploit was NEVER called (scope abort came first)
    mod.check_exploit.assert_not_called()


def test_run_successful_exploit_writes_finding(tmp_targets_dir, monkeypatch):
    """When a session opens, a FindingFact must land in the KB."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    from reverser.kb import for_target
    _write_pidfile(os.getpid())

    client, mod = _setup_run_test(monkeypatch, check_code="vulnerable",
                                   exploit_yields_session=True,
                                   sessions={"7": {"type": "meterpreter",
                                                   "target_host": "10.10.10.5"}})
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })

    kb = for_target("10.10.10.5")
    findings = kb.get_findings(severity="high")
    assert len(findings) == 1
    f = findings[0]
    assert "proftpd_modcopy_exec" in f.title
    assert "10.10.10.5" in f.title
    assert f.severity == "high"
    assert "session" in f.description.lower()


def test_run_failed_exploit_no_finding(tmp_targets_dir, monkeypatch):
    """If no session opens, no finding is written."""
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_run, _write_pidfile
    from reverser.kb import for_target
    _write_pidfile(os.getpid())

    client, mod = _setup_run_test(monkeypatch, check_code="vulnerable",
                                   exploit_yields_session=False,
                                   sessions={})
    with patch("reverser.tools.metasploit._make_msfrpc_client", return_value=client):
        _call(metasploit_run, {
            "module": "exploit/multi/http/proftpd_modcopy_exec",
            "options": {"RHOSTS": "10.10.10.5"},
            "target": "10.10.10.5",
        })

    kb = for_target("10.10.10.5")
    findings = kb.get_findings(severity="high")
    assert len(findings) == 0


# ── metasploit_session ──────────────────────────────────────────────


def test_session_list(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.sessions.list = {
        "1": {"type": "meterpreter", "target_host": "10.10.10.5"},
        "2": {"type": "shell", "target_host": "10.10.10.6"},
    }
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {"action": "list"})

    text = result["content"][0]["text"]
    assert "1" in text and "meterpreter" in text
    assert "2" in text and "shell" in text


def test_session_cmd_captures_output(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_session = MagicMock()
    fake_session.run_with_output.return_value = "root\n"
    fake_client = MagicMock()
    fake_client.sessions.session.return_value = fake_session
    fake_client.sessions.list = {"1": {"type": "shell", "target_host": "10.10.10.5"}}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "cmd",
            "session_id": 1,
            "command": "whoami",
        })

    text = result["content"][0]["text"]
    assert "root" in text


def test_session_cmd_timeout_marked_partial(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_session = MagicMock()
    # Simulate timeout: run_with_output raises a TimeoutError (or generic exc)
    fake_session.run_with_output.side_effect = TimeoutError("timed out")
    fake_client = MagicMock()
    fake_client.sessions.session.return_value = fake_session
    fake_client.sessions.list = {"1": {"type": "shell", "target_host": "10.10.10.5"}}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "cmd",
            "session_id": 1,
            "command": "sleep 1000",
            "timeout_seconds": 5,
        })

    text = result["content"][0]["text"]
    assert "partial" in text.lower() or "timeout" in text.lower()


def test_session_close(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_session = MagicMock()
    fake_client = MagicMock()
    fake_client.sessions.session.return_value = fake_session
    fake_client.sessions.list = {"1": {"type": "shell", "target_host": "10.10.10.5"}}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "close",
            "session_id": 1,
        })

    text = result["content"][0]["text"]
    assert "closed" in text.lower()
    fake_session.stop.assert_called()


def test_session_cmd_requires_session_id(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())
    result = _call(metasploit_session, {"action": "cmd", "command": "whoami"})
    assert result.get("is_error") is True
    assert "session_id" in result["content"][0]["text"].lower()


def test_session_cmd_requires_command(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())
    result = _call(metasploit_session, {"action": "cmd", "session_id": 1})
    assert result.get("is_error") is True
    assert "command" in result["content"][0]["text"].lower()


def test_session_unknown_session(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.metasploit import metasploit_session, _write_pidfile
    _write_pidfile(os.getpid())

    fake_client = MagicMock()
    fake_client.sessions.list = {}
    with patch("reverser.tools.metasploit._make_msfrpc_client",
               return_value=fake_client):
        result = _call(metasploit_session, {
            "action": "cmd",
            "session_id": 999,
            "command": "whoami",
        })

    text = result["content"][0]["text"]
    assert "not_found" in text.lower() or "not found" in text.lower()
