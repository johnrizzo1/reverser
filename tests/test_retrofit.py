"""End-to-end smoke tests verifying retrofitted tools write to the KB.

These tests do NOT exec real binaries — they monkeypatch the run_cmd helper
and feed it canned stdout from the parser fixtures.
"""

import asyncio
from pathlib import Path

import pytest

from reverser.kb import for_target

FIXTURES = Path(__file__).parent / "fixtures" / "parsers"


@pytest.fixture(autouse=True)
def authorize(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def _stub_run_cmd(stdout: str, returncode: int = 0):
    return lambda cmd, **kw: {"stdout": stdout, "stderr": "", "returncode": returncode, "truncated": False}


def test_nbtscan_writes_hosts_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "nbtscan" / "single_host.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.nbtscan_scan, {"target": "10.10.10.5"})
    hosts = for_target("10.10.10.5").get_hosts()
    assert any(h.ip == "10.10.10.5" and h.hostname == "DC01" for h in hosts)


def test_banner_grab_writes_service_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "banner" / "ssh_banner.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.banner_grab, {"target": "10.10.10.5", "port": 22})
    services = for_target("10.10.10.5").get_services()
    assert any(s.port == 22 and "OpenSSH" in (s.banner or "") for s in services)


def test_nmap_scan_writes_hosts_and_services_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "nmap" / "host_with_smb_and_winrm.txt").read_text()
    monkeypatch.setattr(net, "_run_sudo_cmd",
                        lambda cmd, sudo, **kw: {"stdout": text, "stderr": "", "returncode": 0, "truncated": False})

    _call(net.nmap_scan, {"target": "10.10.10.5"})
    kb = for_target("10.10.10.5")
    hosts = kb.get_hosts()
    services = kb.get_services()
    assert any(h.ip == "10.10.10.5" for h in hosts)
    ports = {s.port for s in services}
    assert {53, 88, 445, 5985}.issubset(ports)


def test_ldap_search_writes_dcs_to_kb(tmp_targets_dir):
    """Seam test: feed captured ldap output into the parser + KB directly."""
    from reverser.kb.parsers import parse_ldap_entries
    text = (FIXTURES / "ldap_entries" / "dc_with_users.txt").read_text()
    out = parse_ldap_entries(text)
    assert any(h.is_dc for h in out["hosts"])

    kb = for_target("10.10.10.5")
    for h in out["hosts"]:
        kb.record_host(h)
    hosts = kb.get_hosts()
    assert any(h.is_dc for h in hosts)


def test_ldap_search_has_kb_tail_block():
    """Static check: ldap_search source contains the KB tail block."""
    from reverser.tools import network as net
    import inspect
    handler = getattr(net.ldap_search, "handler", net.ldap_search)
    src = inspect.getsource(handler)
    assert "parse_ldap_entries" in src
    assert "kb.record_host" in src
    assert "logging.getLogger" in src


def test_kerberos_enum_asreproast_writes_creds(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "asreproast" / "two_users.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.kerberos_enum, {
        "target": "10.10.10.5", "domain": "CORP.LOCAL", "mode": "asreproast",
        "username": "alice",
    })
    creds = for_target("10.10.10.5").get_credentials()
    usernames = sorted(c.username for c in creds)
    assert "alice" in usernames and "bob" in usernames
    assert all(c.kerberos_ticket and c.status == "untested" for c in creds)


def test_kerberos_enum_kerberoast_writes_creds(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "kerberoast" / "two_spns.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.kerberos_enum, {
        "target": "10.10.10.5", "domain": "CORP.LOCAL", "mode": "kerberoast",
        "username": "jdoe", "password": "x",
    })
    creds = for_target("10.10.10.5").get_credentials()
    usernames = sorted(c.username for c in creds)
    assert "svc_sql" in usernames and "svc_web" in usernames


def test_smb_enum_writes_host_signing_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    smbclient_text = (FIXTURES / "smbclient_shares" / "auth_listing.txt").read_text()
    nmap_text = (FIXTURES / "nmap_smb_scripts" / "dc01_full.txt").read_text()

    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(smbclient_text))
    monkeypatch.setattr(
        net, "_run_sudo_cmd",
        lambda cmd, sudo, **kw: {"stdout": nmap_text, "stderr": "", "returncode": 0, "truncated": False},
    )

    _call(net.smb_enum, {"target": "10.10.10.5", "mode": "all"})
    kb = for_target("10.10.10.5")
    hosts = kb.get_hosts()
    target_host = next((h for h in hosts if h.ip == "10.10.10.5"), None)
    assert target_host is not None
    assert target_host.smb_signing == "required"
    notes = kb.get_notes()
    assert any("ADMIN" in n or "SCCM_Source" in n for n in notes)


def test_whatweb_fingerprint_writes_service_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import web as webmod
    text = (FIXTURES / "whatweb" / "wordpress_site.txt").read_text()
    monkeypatch.setattr(webmod, "run_cmd", _stub_run_cmd(text))
    monkeypatch.setattr(webmod, "shutil",
                        type("S", (), {"which": staticmethod(lambda _: "/usr/bin/whatweb")})(),
                        raising=False)

    _call(webmod.whatweb_fingerprint, {"target": "http://10.10.10.5"})
    kb = for_target("http://10.10.10.5")
    services = kb.get_services()
    assert any(s.service == "http" for s in services)
    notes = kb.get_notes()
    assert any("WordPress" in n or "wordpress" in n.lower() for n in notes)


def test_nikto_scan_writes_findings_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import web as webmod
    text = (FIXTURES / "nikto" / "multiple_findings.txt").read_text()
    monkeypatch.setattr(webmod, "run_cmd", _stub_run_cmd(text))

    _call(webmod.nikto_scan, {"target": "http://10.10.10.5"})
    kb = for_target("http://10.10.10.5")
    findings = kb.get_findings()
    assert len(findings) >= 5
    assert any("OSVDB" in f.title for f in findings)
