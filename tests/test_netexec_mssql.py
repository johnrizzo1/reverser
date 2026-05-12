"""Tests for netexec_mssql."""

from unittest.mock import AsyncMock, patch

import pytest

from reverser.tools.netexec import netexec_mssql


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(args):
    import asyncio
    fn = getattr(netexec_mssql, "handler", None) or getattr(netexec_mssql, "fn", None) or netexec_mssql
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_mssql_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        _call({"target": "10.10.10.5", "action": "check_auth",
               "username": "sa", "password": "x"})


def test_mssql_check_auth_success(tmp_targets_dir):
    out = "MSSQL    10.10.10.5    1433   DC01   [+] CORP\\sa:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "sa", "password": "Summer2026!", "domain": "CORP",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "sa" for c in valid)


def test_mssql_databases_uses_query(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("MSSQL    [+] sa:x\n[*] master\n[*] tempdb\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "databases",
            "username": "sa", "password": "x", "local_auth": True,
        })
    cmd_str = " ".join(captured["cmd"])
    assert "-q" in captured["cmd"] or "--query" in captured["cmd"] or "sp_databases" in cmd_str.lower()


def test_mssql_xp_cmdshell(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("MSSQL    [+] command output\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "xp_cmdshell",
            "username": "sa", "password": "x", "local_auth": True,
            "command": "whoami",
        })
    assert "-x" in captured["cmd"]
    assert "whoami" in captured["cmd"]


def test_mssql_query_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("MSSQL    [+] result row\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "query",
            "username": "sa", "password": "x", "local_auth": True,
            "query": "SELECT @@version",
        })
    assert "-q" in captured["cmd"] or "--query" in captured["cmd"]
    assert "SELECT @@version" in captured["cmd"]


def test_mssql_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = _call({
        "target": "10.10.10.5", "action": "spray",
        "username": "sa", "password": "x",
    })
    assert result.get("is_error") is True


def test_mssql_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True


def test_mssql_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="sa", password="x", status="valid",
    ))
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok("MSSQL    [+] sa:x\n")):
        result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert "[KB] Using credential: sa" in result["content"][0]["text"]
