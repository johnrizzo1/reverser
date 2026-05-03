"""Fixture-driven tests for KB parsers."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "parsers"


from reverser.kb.parsers import parse_nbtscan_output


def test_parse_nbtscan_single_host():
    text = (FIXTURES / "nbtscan" / "single_host.txt").read_text()
    hosts = parse_nbtscan_output(text)
    assert len(hosts) == 1
    assert hosts[0].ip == "10.10.10.5"
    assert hosts[0].hostname == "DC01"


def test_parse_nbtscan_empty():
    text = (FIXTURES / "nbtscan" / "empty.txt").read_text()
    hosts = parse_nbtscan_output(text)
    assert hosts == []


def test_parse_nbtscan_cidr_range():
    text = (FIXTURES / "nbtscan" / "cidr_range.txt").read_text()
    hosts = parse_nbtscan_output(text)
    ips = sorted(h.ip for h in hosts)
    assert ips == ["192.168.1.10", "192.168.1.20", "192.168.1.21"]
    by_ip = {h.ip: h for h in hosts}
    assert by_ip["192.168.1.10"].hostname == "DC01"
    assert by_ip["192.168.1.20"].hostname == "WS01"


from reverser.kb.parsers import parse_banner_first_line


def test_parse_banner_ssh():
    text = (FIXTURES / "banner" / "ssh_banner.txt").read_text()
    svc = parse_banner_first_line(text, host_ip="10.10.10.5", port=22)
    assert svc is not None
    assert svc.host_ip == "10.10.10.5"
    assert svc.port == 22
    assert svc.proto == "tcp"
    assert "OpenSSH_8.4p1" in (svc.banner or "")


def test_parse_banner_empty():
    text = (FIXTURES / "banner" / "empty.txt").read_text()
    svc = parse_banner_first_line(text, host_ip="10.10.10.5", port=22)
    assert svc is None


def test_parse_banner_http_head():
    text = (FIXTURES / "banner" / "http_head_response.txt").read_text()
    svc = parse_banner_first_line(text, host_ip="10.10.10.5", port=80)
    assert svc is not None
    assert svc.banner is not None
    assert svc.banner.startswith("HTTP/1.1 200 OK")


from reverser.kb.parsers import parse_nmap_output, NmapHostResult


def test_parse_nmap_host_with_smb_and_winrm():
    text = (FIXTURES / "nmap" / "host_with_smb_and_winrm.txt").read_text()
    results = parse_nmap_output(text)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, NmapHostResult)
    assert r.host.ip == "10.10.10.5"
    assert r.host.hostname == "dc01.corp.local"
    assert r.host.os and "Windows" in r.host.os
    ports = {s.port for s in r.services}
    assert {53, 88, 135, 389, 445, 5985}.issubset(ports)
    smb = next(s for s in r.services if s.port == 445)
    assert smb.service == "microsoft-ds"
    assert smb.version and "Windows Server 2019" in smb.version


def test_parse_nmap_no_open_ports():
    text = (FIXTURES / "nmap" / "no_open_ports.txt").read_text()
    results = parse_nmap_output(text)
    assert len(results) == 1
    assert results[0].host.ip == "10.10.10.99"
    assert results[0].services == []


def test_parse_nmap_host_unreachable():
    text = (FIXTURES / "nmap" / "host_unreachable.txt").read_text()
    results = parse_nmap_output(text)
    assert results == []


from reverser.kb.parsers import parse_ldap_entries


def test_parse_ldap_anonymous_rootdse():
    text = (FIXTURES / "ldap_entries" / "anonymous_rootdse.txt").read_text()
    out = parse_ldap_entries(text)
    assert "hosts" in out and "note" in out
    hostnames = [h.hostname for h in out["hosts"] if h.hostname]
    assert "dc01.corp.local" in hostnames
    assert "DC=corp,DC=local" in out["note"]


def test_parse_ldap_empty():
    text = (FIXTURES / "ldap_entries" / "empty_search.txt").read_text()
    out = parse_ldap_entries(text)
    assert out["hosts"] == []
    assert "0" in out["note"] or "empty" in out["note"].lower()


def test_parse_ldap_dc_with_users():
    text = (FIXTURES / "ldap_entries" / "dc_with_users.txt").read_text()
    out = parse_ldap_entries(text)
    hostnames = sorted(h.hostname for h in out["hosts"] if h.hostname)
    assert hostnames == ["dc01.corp.local", "ws01.corp.local", "ws02.corp.local"]
    dc01 = next(h for h in out["hosts"] if h.hostname == "dc01.corp.local")
    assert dc01.is_dc is True
    assert dc01.os and "Windows Server 2019" in dc01.os


from reverser.kb.parsers import parse_asreproast_hashes


def test_parse_asreproast_two_users():
    text = (FIXTURES / "asreproast" / "two_users.txt").read_text()
    creds = parse_asreproast_hashes(text)
    assert len(creds) == 2
    usernames = sorted(c.username for c in creds)
    assert usernames == ["alice", "bob"]
    for c in creds:
        assert c.kerberos_ticket and c.kerberos_ticket.startswith("$krb5asrep$")
        assert c.status == "untested"
        assert c.domain == "CORP.LOCAL"


def test_parse_asreproast_empty():
    text = (FIXTURES / "asreproast" / "empty.txt").read_text()
    creds = parse_asreproast_hashes(text)
    assert creds == []


def test_parse_asreproast_single_user_no_preauth():
    text = (FIXTURES / "asreproast" / "single_user_no_preauth.txt").read_text()
    creds = parse_asreproast_hashes(text)
    assert len(creds) == 1
    assert creds[0].username == "svc_backup"
    assert creds[0].kerberos_ticket
    assert creds[0].kerberos_ticket.startswith("$krb5asrep$")
