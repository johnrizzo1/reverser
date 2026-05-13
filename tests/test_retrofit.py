"""End-to-end smoke tests verifying retrofitted tools write to the KB.

These tests do NOT exec real binaries — they monkeypatch the arun_cmd helper
and feed it canned stdout from the parser fixtures.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

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


def _stub_arun_cmd(stdout: str, returncode: int = 0):
    return AsyncMock(return_value={"stdout": stdout, "stderr": "", "returncode": returncode, "truncated": False})


def _stub_run_sudo_cmd(stdout: str, returncode: int = 0):
    async def _mock(cmd, sudo, **kw):
        return {"stdout": stdout, "stderr": "", "returncode": returncode, "truncated": False}
    return _mock


def test_nbtscan_writes_hosts_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "nbtscan" / "single_host.txt").read_text()
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))

    _call(net.nbtscan_scan, {"target": "10.10.10.5"})
    hosts = for_target("10.10.10.5").get_hosts()
    assert any(h.ip == "10.10.10.5" and h.hostname == "DC01" for h in hosts)


def test_banner_grab_writes_service_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "banner" / "ssh_banner.txt").read_text()
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))

    _call(net.banner_grab, {"target": "10.10.10.5", "port": 22})
    services = for_target("10.10.10.5").get_services()
    assert any(s.port == 22 and "OpenSSH" in (s.banner or "") for s in services)


def test_nmap_scan_writes_hosts_and_services_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "nmap" / "host_with_smb_and_winrm.txt").read_text()
    monkeypatch.setattr(net, "_run_sudo_cmd", _stub_run_sudo_cmd(text))

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
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))

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
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))

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

    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(smbclient_text))
    monkeypatch.setattr(
        net, "_run_sudo_cmd",
        _stub_run_sudo_cmd(nmap_text),
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
    monkeypatch.setattr(webmod, "arun_cmd", _stub_arun_cmd(text))
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
    monkeypatch.setattr(webmod, "arun_cmd", _stub_arun_cmd(text))

    _call(webmod.nikto_scan, {"target": "http://10.10.10.5"})
    kb = for_target("http://10.10.10.5")
    findings = kb.get_findings()
    assert len(findings) >= 5
    assert any("OSVDB" in f.title for f in findings)


def test_gobuster_scan_records_artifact(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "gobuster" / "found_paths.txt").read_text()
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))
    monkeypatch.setattr(net, "_resolve_wordlist", lambda req, default: ("/tmp/fake.txt", None))

    _call(net.gobuster_scan, {"target": "http://10.10.10.5"})
    kb = for_target("http://10.10.10.5")
    arts = kb.get_artifacts()
    assert any(a.kind == "discovered_paths" for a in arts)
    notes = kb.get_notes()
    assert any("/admin" in n for n in notes)


def test_nikto_scan_in_network_writes_findings(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "nikto" / "cve_finding.txt").read_text()
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))

    _call(net.nikto_scan, {"target": "10.10.10.5"})
    findings = for_target("10.10.10.5").get_findings()
    assert any("CVE-" in f.title for f in findings)


def test_ssl_scan_writes_findings(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "ssl" / "expired_cert.txt").read_text()
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))
    monkeypatch.setattr(
        net, "_run_sudo_cmd",
        _stub_run_sudo_cmd(text),
    )

    _call(net.ssl_scan, {"target": "10.13.38.23"})
    findings = for_target("10.13.38.23").get_findings()
    assert any("expired" in f.title.lower() for f in findings)


def test_whatweb_scan_in_network_writes_service(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "whatweb" / "plain_apache.txt").read_text()
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(text))

    _call(net.whatweb_scan, {"target": "http://10.10.10.7"})
    services = for_target("http://10.10.10.7").get_services()
    assert any(s.service == "http" for s in services)


def test_whatweb_scan_falls_back_on_ruby_loaderror(tmp_targets_dir, monkeypatch):
    """When whatweb fails with the Ruby getoptlong LoadError (nixpkgs whatweb
    on Ruby 3.3+), whatweb_scan should delegate to whatweb_fingerprint's
    curl-based fallback instead of returning a confusing error."""
    from reverser.tools import network as net
    from reverser.tools import web as webmod
    from unittest.mock import AsyncMock

    # Make whatweb itself fail with the exact Ruby error
    error_output = (
        "whatweb:37:in '<main>': cannot load such file -- getoptlong (LoadError)"
    )
    monkeypatch.setattr(
        net, "arun_cmd",
        AsyncMock(return_value={
            "stdout": "", "stderr": error_output,
            "returncode": 1, "truncated": False,
        }),
    )

    # Mock the fallback handler so we can verify it gets called
    sentinel_response = {
        "content": [{"type": "text", "text": "FALLBACK_CURL_FINGERPRINT_RESULT"}]
    }
    fallback_calls = []
    async def fake_fingerprint_handler(args):
        fallback_calls.append(args)
        return sentinel_response

    # Patch the whatweb_fingerprint tool's handler attribute
    monkeypatch.setattr(
        webmod.whatweb_fingerprint, "handler", fake_fingerprint_handler,
        raising=False,
    )

    result = _call(net.whatweb_scan, {"target": "http://10.10.10.7"})
    assert len(fallback_calls) == 1
    assert fallback_calls[0] == {"target": "http://10.10.10.7", "aggression": 1}
    assert "FALLBACK_CURL_FINGERPRINT_RESULT" in result["content"][0]["text"]


def test_testssl_analyze_writes_findings(tmp_targets_dir, monkeypatch):
    from reverser.tools import web as webmod
    text = (FIXTURES / "ssl" / "sslscan_full.txt").read_text()
    monkeypatch.setattr(webmod, "arun_cmd", _stub_arun_cmd(text))

    _call(webmod.testssl_analyze, {"target": "10.10.10.5:443"})
    findings = for_target("10.10.10.5:443").get_findings()
    assert any("TLS" in f.title for f in findings)


def test_full_recon_to_kb_show_flow(tmp_targets_dir, monkeypatch):
    """Run nmap → whatweb → kerberos_enum (all stubbed) and verify kb_show
    surfaces everything as a coherent summary."""
    from reverser.tools import network as net
    from reverser.tools import web as webmod
    from reverser.tools.kb import kb_show

    nmap_text = (FIXTURES / "nmap" / "host_with_smb_and_winrm.txt").read_text()
    whatweb_text = (FIXTURES / "whatweb" / "wordpress_site.txt").read_text()
    asrep_text = (FIXTURES / "asreproast" / "two_users.txt").read_text()

    monkeypatch.setattr(
        net, "_run_sudo_cmd",
        _stub_run_sudo_cmd(nmap_text),
    )
    monkeypatch.setattr(net, "arun_cmd", _stub_arun_cmd(asrep_text))
    monkeypatch.setattr(webmod, "arun_cmd", _stub_arun_cmd(whatweb_text))
    monkeypatch.setattr(
        webmod, "shutil",
        type("S", (), {"which": staticmethod(lambda _: "/usr/bin/whatweb")})(),
        raising=False,
    )

    _call(net.nmap_scan, {"target": "10.10.10.5"})
    _call(webmod.whatweb_fingerprint, {"target": "http://10.10.10.5"})
    _call(net.kerberos_enum, {
        "target": "10.10.10.5", "domain": "CORP.LOCAL",
        "mode": "asreproast", "username": "alice",
    })

    result = _call(kb_show, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "Hosts: 1" in text
    assert "Credentials:" in text
    assert "2 total" in text or "2 " in text
