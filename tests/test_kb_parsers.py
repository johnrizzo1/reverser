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
