"""Tests for netexec_ldap."""

from unittest.mock import AsyncMock, patch

import pytest

from reverser.tools.netexec import netexec_ldap


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(args):
    import asyncio
    fn = getattr(netexec_ldap, "handler", None) or getattr(netexec_ldap, "fn", None) or netexec_ldap
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_ldap_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        _call({"target": "10.10.10.5", "action": "users",
               "username": "jdoe", "password": "x"})


def test_ldap_check_auth_success(tmp_targets_dir):
    out = "LDAP    10.10.10.5    389   DC01   [+] CORP.LOCAL\\jdoe:Summer2026!\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP.LOCAL",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)


def test_ldap_users_action_uses_flag(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("LDAP    [+] jdoe:x\nLDAP    user: alice\nLDAP    user: bob\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "users",
            "username": "jdoe", "password": "x", "domain": "CORP.LOCAL",
        })
    assert "--users" in captured["cmd"]


def test_ldap_computers_records_hosts(tmp_targets_dir):
    out = (
        "LDAP    10.10.10.5    389   DC01   [+] CORP.LOCAL\\jdoe:x\n"
        "LDAP    10.10.10.5    389   DC01   DC01.CORP.LOCAL\n"
        "LDAP    10.10.10.6    389   DC01   WS01.CORP.LOCAL\n"
    )
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "computers",
            "username": "jdoe", "password": "x", "domain": "CORP.LOCAL",
        })
    from reverser.kb import for_target
    hosts = for_target("10.10.10.5").get_hosts()
    ips = {h.ip for h in hosts}
    assert "10.10.10.5" in ips
    assert "10.10.10.6" in ips
    by_ip = {h.ip: h for h in hosts}
    assert by_ip["10.10.10.5"].hostname == "DC01"
    assert by_ip["10.10.10.5"].domain == "CORP.LOCAL"


def test_ldap_kerberoastable_action(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("LDAP    [+] jdoe:x\n")
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "kerberoastable",
            "username": "jdoe", "password": "x",
        })
    assert "--kerberoasting" in captured["cmd"] or "kerberoastable" in " ".join(captured["cmd"])


def test_ldap_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = _call({"target": "10.10.10.5", "action": "users"})
    assert result.get("is_error") is True


def test_ldap_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="jdoe", password="x", domain="CORP.LOCAL", status="valid",
    ))
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok("LDAP    [+] ok\n")):
        result = _call({"target": "10.10.10.5", "action": "users"})
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]
