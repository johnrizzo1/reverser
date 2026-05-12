"""End-to-end smoke: simulate an engagement that walks through all 6 netexec tools.

All subprocess calls are mocked. The test verifies KB state after each step
matches what the AD profile prompt expects (creds propagate, hosts get recorded
from LDAP enum, dumps land in loot/, etc.).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from reverser.tools.netexec import (
    netexec_smb, netexec_winrm, netexec_ldap,
    netexec_mssql, netexec_ssh, netexec_ftp_wmi,
)


def _ok(stdout, stderr="", returncode=0):
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode, "truncated": False}


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def test_full_engagement_walkthrough(tmp_targets_dir):
    target = "10.10.10.5"

    # Step 1: SMB check_auth with a found credential
    smb_out = "SMB    10.10.10.5    445   DC01   [+] CORP\\jdoe:Summer2026! (Pwn3d!)\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(smb_out)):
        _call(netexec_smb, {
            "target": target, "action": "check_auth",
            "username": "jdoe", "password": "Summer2026!", "domain": "CORP",
        })

    from reverser.kb import for_target
    kb = for_target(target)
    valid = kb.get_credentials(status="valid")
    assert any(c.username == "jdoe" for c in valid)

    # Step 2: SMB shares — KB fallback should kick in
    shares_out = (
        "SMB    10.10.10.5    445   DC01   [+] CORP\\jdoe:Summer2026!\n"
        "SMB    10.10.10.5    445   DC01   Share           Permissions     Remark\n"
        "SMB    10.10.10.5    445   DC01   -----           -----------     ------\n"
        "SMB    10.10.10.5    445   DC01   ADMIN$          READ,WRITE      Remote Admin\n"
        "SMB    10.10.10.5    445   DC01   IPC$            READ            Remote IPC\n"
    )
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(shares_out)):
        result = _call(netexec_smb, {"target": target, "action": "shares"})
    assert "[KB] Using credential: jdoe" in result["content"][0]["text"]
    assert any("ADMIN$" in n for n in kb.get_notes())

    # Step 3: LDAP computers
    ldap_out = (
        "LDAP    10.10.10.5    389   DC01   [+] CORP.LOCAL\\jdoe:Summer2026!\n"
        "LDAP    10.10.10.5    389   DC01   DC01.CORP.LOCAL\n"
        "LDAP    10.10.10.6    389   DC01   WS01.CORP.LOCAL\n"
    )
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(ldap_out)):
        _call(netexec_ldap, {"target": target, "action": "computers"})
    hosts = kb.get_hosts()
    ips = {h.ip for h in hosts}
    assert "10.10.10.5" in ips
    assert "10.10.10.6" in ips

    # Step 4: WinRM check_auth
    winrm_out = "WINRM    10.10.10.5    5985   DC01   [+] CORP\\jdoe:Summer2026!\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(winrm_out)):
        _call(netexec_winrm, {"target": target, "action": "check_auth"})

    # Step 5: SSH check_auth — different user
    ssh_out = "SSH    10.10.10.5    22   ubuntu   [+] root:rootpw\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(ssh_out)):
        _call(netexec_ssh, {
            "target": target, "action": "check_auth",
            "username": "root", "password": "rootpw",
        })

    # Step 6: MSSQL check_auth (failure)
    mssql_out = "MSSQL    10.10.10.5    1433   DC01   [-] sa:bad STATUS_LOGIN_FAILURE\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(mssql_out)):
        _call(netexec_mssql, {
            "target": target, "action": "check_auth",
            "username": "sa", "password": "bad",
        })
    invalid = kb.get_credentials(status="invalid")
    assert any(c.username == "sa" for c in invalid)

    # Step 7: FTP anonymous check
    ftp_out = "FTP    10.10.10.5    21   FTP   [+] anonymous:anonymous\n"
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(ftp_out)):
        _call(netexec_ftp_wmi, {
            "target": target, "protocol": "ftp", "action": "check_auth",
            "username": "anonymous", "password": "anonymous",
        })

    # Step 8: SMB ntds dump
    ntds_out = (
        "SMB    10.10.10.5    445   DC01   [+] CORP\\jdoe:Summer2026!\n"
        "SMB    10.10.10.5    445   DC01   [+] Dumping NTDS\n"
        "Administrator:500:aad3b435b51404eeaad3b435b51404ee:8846f7eaee8fb117ad06bdd830b7586c:::\n"
        "krbtgt:502:aad3b435b51404eeaad3b435b51404ee:31d6cfe0d16ae931b73c59d7e0c089c0:::\n"
    )
    with patch("reverser.tools.netexec.arun_cmd", new_callable=AsyncMock, return_value=_ok(ntds_out)):
        _call(netexec_smb, {
            "target": target, "action": "ntds",
            "username": "Administrator", "nt_hash": "8846f7eaee8fb117ad06bdd830b7586c",
            "local_auth": True,
        })
    artifacts = kb.get_artifacts()
    assert any(a.kind == "ntds_dump" for a in artifacts)
    untested = kb.get_credentials(status="untested")
    untested_users = {c.username for c in untested}
    assert "krbtgt" in untested_users

    # Final state inspection
    all_creds = kb.get_credentials()
    by_user = {c.username for c in all_creds}
    assert "jdoe" in by_user
    assert "root" in by_user
    assert "anonymous" in by_user
    assert "sa" in by_user
    assert "Administrator" in by_user
    assert "krbtgt" in by_user
