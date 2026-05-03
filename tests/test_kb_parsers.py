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


from reverser.kb.parsers import parse_kerberoast_hashes


def test_parse_kerberoast_two_spns():
    text = (FIXTURES / "kerberoast" / "two_spns.txt").read_text()
    creds = parse_kerberoast_hashes(text)
    assert len(creds) == 2
    usernames = sorted(c.username for c in creds)
    assert usernames == ["svc_sql", "svc_web"]
    for c in creds:
        assert c.kerberos_ticket and c.kerberos_ticket.startswith("$krb5tgs$")
        assert c.status == "untested"
        assert c.domain == "CORP.LOCAL"


def test_parse_kerberoast_empty():
    text = (FIXTURES / "kerberoast" / "empty.txt").read_text()
    assert parse_kerberoast_hashes(text) == []


def test_parse_kerberoast_sql_service():
    text = (FIXTURES / "kerberoast" / "sql_service.txt").read_text()
    creds = parse_kerberoast_hashes(text)
    assert len(creds) == 1
    assert creds[0].username == "svc_sql"
    assert creds[0].kerberos_ticket
    assert "MSSQLSvc" in creds[0].kerberos_ticket


from reverser.kb.parsers import parse_smbclient_shares


def test_parse_smbclient_anonymous():
    text = (FIXTURES / "smbclient_shares" / "anonymous_listing.txt").read_text()
    out = parse_smbclient_shares(text)
    assert "host" in out and "shares_note" in out
    assert "ADMIN$" in out["shares_note"]
    assert "IPC$" in out["shares_note"]
    assert out["host"].smb_signing in (None, "disabled", "enabled", "required")


def test_parse_smbclient_access_denied():
    text = (FIXTURES / "smbclient_shares" / "access_denied.txt").read_text()
    out = parse_smbclient_shares(text)
    assert out["shares_note"]
    assert "ACCESS_DENIED" in out["shares_note"]


def test_parse_smbclient_auth_listing():
    text = (FIXTURES / "smbclient_shares" / "auth_listing.txt").read_text()
    out = parse_smbclient_shares(text)
    assert "Backups" in out["shares_note"]
    assert "SCCM_Source" in out["shares_note"]
    assert out["host"].domain == "CORP" or "CORP" in out["shares_note"]


from reverser.kb.parsers import parse_nmap_smb_scripts


def test_parse_nmap_smb_dc01():
    text = (FIXTURES / "nmap_smb_scripts" / "dc01_full.txt").read_text()
    out = parse_nmap_smb_scripts(text)
    assert out["host"].ip == "10.10.10.5"
    assert out["host"].hostname == "dc01.corp.local"
    assert out["host"].domain == "corp.local"
    assert out["host"].smb_signing == "required"
    ports = {s.port for s in out["services"]}
    assert 445 in ports
    assert "ADMIN$" in out["note"]


def test_parse_nmap_smb_no_smb():
    text = (FIXTURES / "nmap_smb_scripts" / "no_smb.txt").read_text()
    out = parse_nmap_smb_scripts(text)
    assert out["services"] == []
    assert out["host"].ip == "10.10.10.99"


def test_parse_nmap_smb_signing_disabled():
    text = (FIXTURES / "nmap_smb_scripts" / "signing_disabled.txt").read_text()
    out = parse_nmap_smb_scripts(text)
    assert out["host"].smb_signing == "disabled"
    assert out["host"].hostname == "ws01.corp.local"


from reverser.kb.parsers import parse_whatweb_plugins


def test_parse_whatweb_wordpress():
    text = (FIXTURES / "whatweb" / "wordpress_site.txt").read_text()
    out = parse_whatweb_plugins(text, host_ip="10.10.10.5", port=80)
    assert "service" in out and "note" in out
    svc = out["service"]
    assert svc.host_ip == "10.10.10.5"
    assert svc.port == 80
    assert svc.proto == "tcp"
    assert svc.service == "http"
    assert "WordPress" in out["note"]
    assert "Apache" in out["note"]


def test_parse_whatweb_empty():
    text = (FIXTURES / "whatweb" / "empty.txt").read_text()
    out = parse_whatweb_plugins(text, host_ip="10.10.10.5", port=80)
    assert out["service"] is None or out["note"] == ""


def test_parse_whatweb_plain_apache():
    text = (FIXTURES / "whatweb" / "plain_apache.txt").read_text()
    out = parse_whatweb_plugins(text, host_ip="10.10.10.7", port=80)
    assert "Apache" in out["note"]
    assert out["service"].version is None or "Apache" in out["service"].version


from reverser.kb.parsers import parse_gobuster_paths


def test_parse_gobuster_found():
    text = (FIXTURES / "gobuster" / "found_paths.txt").read_text()
    paths = parse_gobuster_paths(text)
    assert "/admin" in paths
    assert "/index.html" in paths
    assert "/robots.txt" in paths
    assert len(paths) == 5


def test_parse_gobuster_empty():
    text = (FIXTURES / "gobuster" / "empty.txt").read_text()
    assert parse_gobuster_paths(text) == []


def test_parse_gobuster_with_status_filter():
    text = (FIXTURES / "gobuster" / "with_status_filter.txt").read_text()
    paths = parse_gobuster_paths(text)
    assert paths == ["/api", "/api/v1", "/dashboard"]
