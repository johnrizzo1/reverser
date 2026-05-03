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
