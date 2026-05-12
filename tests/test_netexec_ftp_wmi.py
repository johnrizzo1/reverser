"""Tests for netexec_ftp_wmi."""

from unittest.mock import AsyncMock, patch

import pytest

from reverser.tools.netexec import netexec_ftp_wmi


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(args):
    import asyncio
    fn = getattr(netexec_ftp_wmi, "handler", None) or getattr(netexec_ftp_wmi, "fn", None) or netexec_ftp_wmi
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_ftp_wmi_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        _call({"target": "10.10.10.5", "protocol": "ftp",
               "action": "check_auth",
               "username": "anonymous", "password": "anonymous"})


def test_ftp_check_auth_success(tmp_targets_dir):
    out = "FTP    10.10.10.5    21   FTP-SVR   [+] anonymous:anonymous\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "protocol": "ftp", "action": "check_auth",
            "username": "anonymous", "password": "anonymous",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "anonymous" for c in valid)


def test_ftp_list_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("FTP    [+] ok\nFTP    file1.txt\nFTP    file2.txt\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "protocol": "ftp", "action": "list",
            "username": "anonymous", "password": "anonymous",
        })
    cmd_str = " ".join(captured["cmd"])
    assert "ls" in cmd_str.lower() or "--ls" in cmd_str


def test_wmi_exec_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("WMI    10.10.10.5    135   DC01   [+] command output\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "protocol": "wmi", "action": "exec",
            "username": "jdoe", "password": "x", "command": "whoami",
        })
    assert "wmi" in captured["cmd"]
    assert "-x" in captured["cmd"]
    assert "whoami" in captured["cmd"]


def test_wmi_check_auth_records_creds(tmp_targets_dir):
    out = "WMI    10.10.10.5    135   DC01   [+] CORP\\jdoe:Summer2026!\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "protocol": "wmi", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)


def test_invalid_protocol_returns_error(tmp_targets_dir):
    result = _call({
        "target": "10.10.10.5", "protocol": "rdp", "action": "check_auth",
        "username": "x", "password": "y",
    })
    assert result.get("is_error") is True
    assert "protocol" in result["content"][0]["text"].lower()


def test_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = _call({
        "target": "10.10.10.5", "protocol": "ftp", "action": "check_auth",
    })
    assert result.get("is_error") is True


def test_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="anonymous", password="anonymous", status="valid",
    ))
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok("FTP    [+] ok\n")):
        result = _call({
            "target": "10.10.10.5", "protocol": "ftp", "action": "check_auth",
        })
    assert "[KB] Using credential: anonymous" in result["content"][0]["text"]
