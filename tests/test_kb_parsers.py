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
