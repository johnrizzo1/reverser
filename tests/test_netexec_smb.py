"""Tests for netexec_smb."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_smb


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(args):
    """Invoke the SDK-wrapped netexec_smb tool."""
    import asyncio
    fn = getattr(netexec_smb, "handler", None) or getattr(netexec_smb, "fn", None) or netexec_smb
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_smb_unauthorized_raises(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        _call({"target": "10.10.10.5", "action": "check_auth",
               "username": "jdoe", "password": "x"})


def test_smb_check_auth_success_records_valid_cred(tmp_targets_dir):
    out = "SMB    10.10.10.5    445   DC01   [+] CORP.LOCAL\\jdoe:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        result = _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })
    assert "is_error" not in result or not result["is_error"]
    text = result["content"][0]["text"]
    assert "Pwn3d" in text or "+" in text

    from reverser.kb import for_target
    creds = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in creds)


def test_smb_check_auth_failure_records_invalid_cred(tmp_targets_dir):
    out = "SMB    10.10.10.5    445   DC01   [-] CORP\\jdoe:bad STATUS_LOGON_FAILURE\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "bad", "domain": "CORP",
        })
    from reverser.kb import for_target
    invalid = for_target("10.10.10.5").get_credentials(status="invalid")
    assert any(c.username == "jdoe" for c in invalid)


def test_smb_no_creds_uses_kb_fallback(tmp_targets_dir):
    """No creds in args + valid cred in KB → uses the KB cred."""
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="jdoe", password="Summer2026!", status="valid",
    ))

    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("SMB    10.10.10.5    445   DC01   [+] jdoe:Summer2026!\n")

    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        result = _call({"target": "10.10.10.5", "action": "check_auth"})

    assert "-u" in captured["cmd"] and "jdoe" in captured["cmd"]
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]


def test_smb_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True
    assert "no valid credentials" in result["content"][0]["text"].lower()


def test_smb_shares_records_note(tmp_targets_dir):
    out = (
        "SMB    10.10.10.5    445   DC01   Share           Permissions     Remark\n"
        "SMB    10.10.10.5    445   DC01   -----           -----------     ------\n"
        "SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin\n"
        "SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "shares",
            "username": "jdoe", "password": "x",
        })
    from reverser.kb import for_target
    notes = for_target("10.10.10.5").get_notes()
    assert any("ADMIN$" in n for n in notes)


def test_smb_ntds_dump_saves_artifact_and_creds(tmp_targets_dir):
    out = (
        "[+] Dumping NTDS\n"
        "Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::\n"
        "krbtgt:502:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::\n"
    )
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "ntds",
            "username": "admin", "password": "x", "local_auth": True,
        })
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    arts = kb.get_artifacts()
    assert any(a.kind == "ntds_dump" for a in arts)
    assert any(a.source_tool == "netexec_smb" for a in arts)
    untested = kb.get_credentials(status="untested")
    usernames = [c.username for c in untested]
    assert "Administrator" in usernames
    assert "krbtgt" in usernames


def test_smb_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = _call({
        "target": "10.10.10.5", "action": "spray",
        "username": "jdoe", "password": "Summer2026!",
    })
    assert result.get("is_error") is True
    assert "REVERSER_AD_ALLOW_SPRAY" in result["content"][0]["text"]


def test_smb_spray_caps_attempts(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_AD_ALLOW_SPRAY", "1")
    monkeypatch.setenv("REVERSER_SPRAY_MAX", "2")
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "spray",
            "username": "jdoe", "password": "Summer2026!",
        })
    cmd_str = " ".join(captured["cmd"])
    assert "2" in cmd_str
    assert "--continue-on-success" not in cmd_str


def test_smb_module_invocation(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("[*] module ran")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "exec",
            "username": "jdoe", "password": "x",
            "module": "lsassy",
        })
    cmd = captured["cmd"]
    assert "-M" in cmd and "lsassy" in cmd
