"""Tests for netexec_ssh."""

from unittest.mock import patch

import pytest

from reverser.tools.netexec import netexec_ssh


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(args):
    import asyncio
    fn = getattr(netexec_ssh, "handler", None) or getattr(netexec_ssh, "fn", None) or netexec_ssh
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_ssh_unauthorized(monkeypatch, tmp_targets_dir):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir.parent)
    from reverser.kb import AuthorizationError
    with pytest.raises(AuthorizationError):
        _call({"target": "10.10.10.5", "action": "check_auth",
               "username": "root", "password": "x"})


def test_ssh_check_auth_success(tmp_targets_dir):
    out = "SSH    10.10.10.5    22   ubuntu   [+] root:Summer2026!\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "root", "password": "Summer2026!",
        })
    from reverser.kb import for_target
    valid = for_target("10.10.10.5").get_credentials(status="valid")
    assert any(c.username == "root" for c in valid)


def test_ssh_check_auth_failure_records_invalid(tmp_targets_dir):
    out = "SSH    10.10.10.5    22   ubuntu   [-] root:bad\n"
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok(out)):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "root", "password": "bad",
        })
    from reverser.kb import for_target
    invalid = for_target("10.10.10.5").get_credentials(status="invalid")
    assert any(c.username == "root" for c in invalid)


def test_ssh_key_file_passed(tmp_targets_dir, tmp_path):
    keyfile = tmp_path / "id_rsa"
    keyfile.write_text("KEY")
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("SSH    [+] root:KEY\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "check_auth",
            "username": "root", "key_file": str(keyfile),
        })
    cmd_str = " ".join(captured["cmd"])
    assert str(keyfile) in cmd_str


def test_ssh_exec_passes_command(tmp_targets_dir):
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _ok("SSH    [+] uid=0\n")
    with patch("reverser.tools.netexec.run_cmd", side_effect=fake_run):
        _call({
            "target": "10.10.10.5", "action": "exec",
            "username": "root", "password": "x", "command": "id",
        })
    assert "-x" in captured["cmd"]
    assert "id" in captured["cmd"]


def test_ssh_spray_blocked_without_env(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_AD_ALLOW_SPRAY", raising=False)
    result = _call({
        "target": "10.10.10.5", "action": "spray",
        "username": "root", "password": "x",
    })
    assert result.get("is_error") is True


def test_ssh_no_creds_no_kb_returns_error(tmp_targets_dir):
    result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert result.get("is_error") is True


def test_ssh_kb_fallback(tmp_targets_dir):
    from reverser.kb import for_target, CredentialFact
    for_target("10.10.10.5").record_credential(CredentialFact(
        username="ubuntu", password="x", status="valid",
    ))
    with patch("reverser.tools.netexec.run_cmd", return_value=_ok("SSH    [+] ok\n")):
        result = _call({"target": "10.10.10.5", "action": "check_auth"})
    assert "[KB] Using credential: ubuntu" in result["content"][0]["text"]
