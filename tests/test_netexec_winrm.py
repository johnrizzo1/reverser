"""Tests for netexec_winrm."""

from unittest.mock import AsyncMock, patch

import pytest

from reverser.tools.netexec import netexec_winrm


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(args):
    import asyncio
    fn = getattr(netexec_winrm, "handler", None) or getattr(netexec_winrm, "fn", None) or netexec_winrm
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_winrm_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        _call({"target": "10.10.10.5", "action": "check_auth",
               "username": "jdoe", "password": "x"})


def test_winrm_check_auth_success(tmp_targets_dir):
    out = "WINRM    10.10.10.5    5985   DC01   [+] CORP\\jdoe:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)


def test_winrm_check_auth_failure_records_invalid(tmp_targets_dir):
    out = "WINRM    10.10.10.5    5985   DC01   [-] CORP\\jdoe:bad\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "bad", "domain": "CORP",
        })
    from reverser.kb import for_target
    invalid = for_target("10.10.10.5").get_credentials(status="invalid")
    assert any(c.username == "jdoe" for c in invalid)


def test_winrm_exec_passes_command(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("WINRM    [+] whoami: nt authority\\system\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "exec",
            "username": "jdoe", "password": "x",
            "command": "whoami",
        })
    assert "-x" in captured["cmd"]
    assert "whoami" in captured["cmd"]


def test_winrm_ps_uses_ps_flag(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("[+] ran")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "ps",
            "username": "jdoe", "password": "x",
            "command": "Get-Process",
        })
    assert "-X" in captured["cmd"]
    assert "Get-Process" in captured["cmd"]


def test_winrm_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = _call({
        "target": "10.10.10.5", "action": "spray",
        "username": "jdoe", "password": "Summer2026!",
    })
    assert result.get("is_error") is True


def test_winrm_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True


def test_winrm_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="jdoe", password="x", status="valid",
    ))
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("WINRM    [+] jdoe:x\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert "jdoe" in captured["cmd"]
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]
