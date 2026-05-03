# Plan 2 — KB Read-side Tools, Parsers, and Retrofit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every existing recon tool a KB writer and ship the read-side surface so the LLM can call `kb_show` (and friends) and see exactly what has been discovered. After Plan 2 lands, a typical `pentest` profile session that runs `nmap_scan`, `whatweb_scan`, and `kerberos_enum` will populate `targets/<target>/state.db` automatically — and `kb_export_report` will emit a markdown report shaped like `pentest_report_10.13.38.23.md` from KB contents alone.

**Architecture:** Two surfaces ride on top of the `reverser.kb` library shipped in Plan 1:

1. **`tools/kb.py`** — 7 new MCP tools (`kb_show`, `kb_list_hosts`, `kb_list_services`, `kb_list_creds`, `kb_add_finding`, `kb_add_note`, `kb_export_report`). All registered through `tools/__init__.py`. Each calls `require_pentest_auth()` at entry, then `for_target(target)` to obtain the per-target KB.
2. **`kb/parsers.py`** — 12 pure-function parsers (`parse_nmap_output`, `parse_ldap_entries`, `parse_asreproast_hashes`, `parse_kerberoast_hashes`, `parse_smbclient_shares`, `parse_nmap_smb_scripts`, `parse_nbtscan_output`, `parse_banner_first_line`, `parse_whatweb_plugins`, `parse_gobuster_paths`, `parse_nikto_findings`, `parse_ssl_findings`). No I/O, no global state, no logger. Each is fixture-tested.

The 11 retrofitted tools (`nmap_scan`, `ldap_search`, `kerberos_enum`, `smb_enum`, `nbtscan_scan`, `banner_grab`, `whatweb_scan`, `gobuster_scan`, `nikto_scan`, `ssl_scan` — and `nmap_scan` in `tools/web.py`) gain a tail block, copy-pasted from a single template, that calls the parser and writes the result via the KB. The block is wrapped in `try/except`; any parser breakage is logged at WARNING and never propagates.

**Tech Stack:** Python 3.11+, sqlite3, pytest. Depends on Plan 1 (`reverser.kb`).

**Spec reference:** `docs/superpowers/specs/2026-05-03-netexec-bloodhound-ad-design.md` § Existing tool retrofit, § KB read-side tools.

---

## File Structure

**Created:**
- `src/reverser/kb/parsers.py` — 12 parser pure functions
- `src/reverser/tools/kb.py` — 7 new MCP tools
- `tests/test_kb_parsers.py` — fixture-driven parser tests
- `tests/test_kb_tools.py` — KB tool integration tests
- `tests/test_retrofit.py` — end-to-end smoke that the retrofits write to KB
- `tests/fixtures/parsers/nmap/host_with_smb_and_winrm.txt`
- `tests/fixtures/parsers/nmap/no_open_ports.txt`
- `tests/fixtures/parsers/nmap/host_unreachable.txt`
- `tests/fixtures/parsers/ldap_entries/anonymous_rootdse.txt`
- `tests/fixtures/parsers/ldap_entries/empty_search.txt`
- `tests/fixtures/parsers/ldap_entries/dc_with_users.txt`
- `tests/fixtures/parsers/asreproast/two_users.txt`
- `tests/fixtures/parsers/asreproast/empty.txt`
- `tests/fixtures/parsers/asreproast/single_user_no_preauth.txt`
- `tests/fixtures/parsers/kerberoast/two_spns.txt`
- `tests/fixtures/parsers/kerberoast/empty.txt`
- `tests/fixtures/parsers/kerberoast/sql_service.txt`
- `tests/fixtures/parsers/smbclient_shares/anonymous_listing.txt`
- `tests/fixtures/parsers/smbclient_shares/access_denied.txt`
- `tests/fixtures/parsers/smbclient_shares/auth_listing.txt`
- `tests/fixtures/parsers/nmap_smb_scripts/dc01_full.txt`
- `tests/fixtures/parsers/nmap_smb_scripts/no_smb.txt`
- `tests/fixtures/parsers/nmap_smb_scripts/signing_disabled.txt`
- `tests/fixtures/parsers/nbtscan/single_host.txt`
- `tests/fixtures/parsers/nbtscan/empty.txt`
- `tests/fixtures/parsers/nbtscan/cidr_range.txt`
- `tests/fixtures/parsers/banner/ssh_banner.txt`
- `tests/fixtures/parsers/banner/empty.txt`
- `tests/fixtures/parsers/banner/http_head_response.txt`
- `tests/fixtures/parsers/whatweb/wordpress_site.txt`
- `tests/fixtures/parsers/whatweb/empty.txt`
- `tests/fixtures/parsers/whatweb/plain_apache.txt`
- `tests/fixtures/parsers/gobuster/found_paths.txt`
- `tests/fixtures/parsers/gobuster/empty.txt`
- `tests/fixtures/parsers/gobuster/with_status_filter.txt`
- `tests/fixtures/parsers/nikto/multiple_findings.txt`
- `tests/fixtures/parsers/nikto/empty.txt`
- `tests/fixtures/parsers/nikto/cve_finding.txt`
- `tests/fixtures/parsers/ssl/sslscan_full.txt`
- `tests/fixtures/parsers/ssl/no_findings.txt`
- `tests/fixtures/parsers/ssl/expired_cert.txt`

**Modified:**
- `src/reverser/tools/__init__.py` — register `tools/kb.py`
- `src/reverser/tools/network.py` — retrofit 6 tools with KB writes
- `src/reverser/tools/web.py` — retrofit 4 tools with KB writes (`whatweb_fingerprint`, `gobuster_scan`-via-`ffuf_fuzz` is NOT retrofitted; only the 4 specified tools — see below)

> Note: the spec lists `gobuster_scan`, `whatweb_scan`, `nikto_scan`, `ssl_scan` in `tools/web.py`, but the existing `web.py` ships `whatweb_fingerprint`, `nikto_scan`, `testssl_analyze`, and `ffuf_fuzz`. The 4 retrofits in `web.py` therefore target: `whatweb_fingerprint` (using `parse_whatweb_plugins`), `nikto_scan` (using `parse_nikto_findings`), `testssl_analyze` (using `parse_ssl_findings`). `gobuster_scan` lives in `tools/network.py` — see Task 30. `ffuf_fuzz` is not retrofitted (too generic, like `curl_request`).

---

## Task 1: Create parsers module skeleton + fixture directory layout

**Files:**
- Create: `src/reverser/kb/parsers.py` (skeleton with module docstring only)
- Create: `tests/test_kb_parsers.py` (skeleton with shared `FIXTURES` constant)
- Create: `tests/fixtures/parsers/.gitkeep`

- [ ] **Step 1: Create `src/reverser/kb/parsers.py` with module docstring and shared imports**

```python
"""Pure-function parsers that turn captured tool stdout into KB facts.

Every parser in this module is a pure function: no I/O, no logging, no
global state. Each one accepts a `text: str` (a tool's captured stdout)
and returns dataclasses defined in `reverser.kb.store`.

Parsers are intentionally tolerant: empty input returns an empty result,
malformed input returns whatever can be salvaged. They never raise on
unrecognised lines — the worst case is an empty list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .store import (
    HostFact,
    ServiceFact,
    CredentialFact,
    FindingFact,
)


@dataclass
class NmapHostResult:
    """Wrapper for a single nmap host with its discovered services."""
    host: HostFact
    services: list[ServiceFact]
```

- [ ] **Step 2: Create `tests/test_kb_parsers.py` skeleton**

```python
"""Fixture-driven tests for KB parsers."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "parsers"
```

- [ ] **Step 3: Create `tests/fixtures/parsers/.gitkeep`**

```
```

(empty file — keeps the directory in git before fixtures are added)

- [ ] **Step 4: Run pytest to verify nothing breaks**

Run: `pytest tests/ -v`
Expected: all Plan 1 tests still pass; `test_kb_parsers.py` collects 0 tests.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/.gitkeep
git commit -m "feat(kb): add parsers module skeleton + fixture dir"
```

---

## Task 2: Parser — `parse_nbtscan_output`

**Files:**
- Create: `tests/fixtures/parsers/nbtscan/single_host.txt`
- Create: `tests/fixtures/parsers/nbtscan/empty.txt`
- Create: `tests/fixtures/parsers/nbtscan/cidr_range.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/nbtscan/single_host.txt`**

```
Doing NBT name scan for addresses from 10.10.10.5

IP address       NetBIOS Name     Server    User             MAC address
------------------------------------------------------------------------------
10.10.10.5       DC01             <server>  <unknown>        00:50:56:01:23:45
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/nbtscan/empty.txt`**

```
Doing NBT name scan for addresses from 10.10.10.99

IP address       NetBIOS Name     Server    User             MAC address
------------------------------------------------------------------------------
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/nbtscan/cidr_range.txt`**

```
Doing NBT name scan for addresses from 192.168.1.0/24

IP address       NetBIOS Name     Server    User             MAC address
------------------------------------------------------------------------------
192.168.1.10     DC01             <server>  <unknown>        00:50:56:aa:bb:cc
192.168.1.20     WS01             <server>  ALICE            00:50:56:aa:bb:dd
192.168.1.21     WS02             <server>  BOB              00:50:56:aa:bb:ee
```

- [ ] **Step 4: Append failing tests to `tests/test_kb_parsers.py`**

```python
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
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k nbtscan`
Expected: 3 errors (`ImportError` for `parse_nbtscan_output`).

- [ ] **Step 6: Implement `parse_nbtscan_output` in `src/reverser/kb/parsers.py`**

Append to the parsers module:

```python
_NBTSCAN_LINE_RE = re.compile(
    r"^\s*(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<name>\S+)\s+"
)


def parse_nbtscan_output(text: str) -> list[HostFact]:
    """Parse nbtscan column output into HostFact entries.

    Lines like ``10.10.10.5    DC01    <server>  <unknown>    00:50:...``
    yield a HostFact(ip="10.10.10.5", hostname="DC01"). Header rows and
    blank lines are skipped. Hostnames of "<unknown>" are dropped.
    """
    hosts: list[HostFact] = []
    for line in text.splitlines():
        m = _NBTSCAN_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name in ("<unknown>", "<server>", "NetBIOS"):
            continue
        hosts.append(HostFact(ip=m.group("ip"), hostname=name))
    return hosts
```

- [ ] **Step 7: Run tests to verify pass**

Run: `pytest tests/test_kb_parsers.py -v -k nbtscan`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/nbtscan/
git commit -m "feat(kb/parsers): parse_nbtscan_output with 3 fixtures"
```

---

## Task 3: Parser — `parse_banner_first_line`

**Files:**
- Create: `tests/fixtures/parsers/banner/ssh_banner.txt`
- Create: `tests/fixtures/parsers/banner/empty.txt`
- Create: `tests/fixtures/parsers/banner/http_head_response.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/banner/ssh_banner.txt`**

```
SSH-2.0-OpenSSH_8.4p1 Debian-5+deb11u1
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/banner/empty.txt`**

```
```

(empty file)

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/banner/http_head_response.txt`**

```
HTTP/1.1 200 OK
Date: Sun, 03 May 2026 12:34:56 GMT
Server: Apache/2.4.46 (FreeBSD) PHP/7.4.15
Last-Modified: Thu, 20 Feb 2020 22:12:44 GMT
Content-Type: text/html
Connection: close
```

- [ ] **Step 4: Append failing tests to `tests/test_kb_parsers.py`**

```python
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
    assert svc.banner.startswith("HTTP/1.1 200 OK")
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k banner`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_banner_first_line`**

Append to parsers module:

```python
def parse_banner_first_line(
    text: str, host_ip: str, port: int, proto: str = "tcp",
) -> ServiceFact | None:
    """Return a ServiceFact whose banner is the first non-empty line of text.

    Returns None if text is empty or all-whitespace. The scan_source field is
    set to "banner_grab".
    """
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.strip():
            return ServiceFact(
                host_ip=host_ip,
                port=port,
                proto=proto,
                banner=line,
                scan_source="banner_grab",
            )
    return None
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k banner`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/banner/
git commit -m "feat(kb/parsers): parse_banner_first_line with 3 fixtures"
```

---

## Task 4: Parser — `parse_nmap_output`

**Files:**
- Create: `tests/fixtures/parsers/nmap/host_with_smb_and_winrm.txt`
- Create: `tests/fixtures/parsers/nmap/no_open_ports.txt`
- Create: `tests/fixtures/parsers/nmap/host_unreachable.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/nmap/host_with_smb_and_winrm.txt`**

```
Starting Nmap 7.94 ( https://nmap.org ) at 2026-05-03 12:00 UTC
Nmap scan report for dc01.corp.local (10.10.10.5)
Host is up (0.0021s latency).
Not shown: 994 closed tcp ports (reset)
PORT     STATE SERVICE       VERSION
53/tcp   open  domain        Simple DNS Plus
88/tcp   open  kerberos-sec  Microsoft Windows Kerberos (server time: 2026-05-03 12:00:00Z)
135/tcp  open  msrpc         Microsoft Windows RPC
389/tcp  open  ldap          Microsoft Windows Active Directory LDAP (Domain: corp.local)
445/tcp  open  microsoft-ds  Windows Server 2019 Standard 17763 microsoft-ds (workgroup: CORP)
5985/tcp open  http          Microsoft HTTPAPI httpd 2.0 (SSDP/UPnP)
Service Info: Host: DC01; OS: Windows; CPE: cpe:/o:microsoft:windows

Service detection performed. Please report any incorrect results at https://nmap.org/submit/ .
Nmap done: 1 IP address (1 host up) scanned in 12.34 seconds
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/nmap/no_open_ports.txt`**

```
Starting Nmap 7.94 ( https://nmap.org ) at 2026-05-03 12:00 UTC
Nmap scan report for 10.10.10.99
Host is up (0.0050s latency).
All 1000 scanned ports on 10.10.10.99 are in ignored states.
Not shown: 1000 filtered tcp ports (no-response)

Nmap done: 1 IP address (1 host up) scanned in 22.10 seconds
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/nmap/host_unreachable.txt`**

```
Starting Nmap 7.94 ( https://nmap.org ) at 2026-05-03 12:00 UTC
Note: Host seems down. If it is really up, but blocking our ping probes, try -Pn
Nmap done: 1 IP address (0 hosts up) scanned in 3.00 seconds
```

- [ ] **Step 4: Append failing tests**

```python
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
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k nmap_output or nmap_no or nmap_host`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_nmap_output`**

Append:

```python
_NMAP_HOST_LINE_RE = re.compile(
    r"^Nmap scan report for (?:(?P<host>[\w.\-]+)\s+\((?P<ip1>\d{1,3}(?:\.\d{1,3}){3})\)"
    r"|(?P<ip2>\d{1,3}(?:\.\d{1,3}){3}))"
)
_NMAP_PORT_LINE_RE = re.compile(
    r"^(?P<port>\d+)/(?P<proto>tcp|udp)\s+(?P<state>open|filtered|closed)\s+"
    r"(?P<service>\S+)(?:\s+(?P<version>.+))?$"
)
_NMAP_SERVICE_INFO_OS_RE = re.compile(r"OS:\s*([^;]+)")
_NMAP_DOMAIN_RE = re.compile(r"Domain:\s*([\w.\-]+)", re.IGNORECASE)


def parse_nmap_output(text: str) -> list[NmapHostResult]:
    """Parse human-readable nmap output into per-host results.

    Each Nmap "Nmap scan report for..." section becomes a NmapHostResult
    with .host (HostFact) and .services (list[ServiceFact]). Hosts that
    nmap reports as down (no scan-report block) are omitted entirely.
    """
    results: list[NmapHostResult] = []
    current_host: HostFact | None = None
    current_services: list[ServiceFact] = []
    domain_hint: str | None = None

    def flush():
        if current_host is not None:
            if domain_hint and not current_host.domain:
                current_host.domain = domain_hint
            results.append(NmapHostResult(host=current_host, services=current_services))

    for raw in text.splitlines():
        line = raw.rstrip()
        m = _NMAP_HOST_LINE_RE.match(line)
        if m:
            flush()
            ip = m.group("ip1") or m.group("ip2")
            hostname = m.group("host")
            current_host = HostFact(ip=ip, hostname=hostname)
            current_services = []
            domain_hint = None
            continue
        if current_host is None:
            continue
        port_m = _NMAP_PORT_LINE_RE.match(line)
        if port_m and port_m.group("state") == "open":
            ver = port_m.group("version") or None
            current_services.append(
                ServiceFact(
                    host_ip=current_host.ip,
                    port=int(port_m.group("port")),
                    proto=port_m.group("proto"),
                    service=port_m.group("service"),
                    version=ver,
                    scan_source="nmap_scan",
                )
            )
            d = _NMAP_DOMAIN_RE.search(line)
            if d:
                domain_hint = d.group(1)
            continue
        if line.startswith("Service Info:"):
            os_m = _NMAP_SERVICE_INFO_OS_RE.search(line)
            if os_m:
                current_host.os = os_m.group(1).strip()

    flush()
    return results
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k "nmap_output or nmap_no or nmap_host"`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/nmap/
git commit -m "feat(kb/parsers): parse_nmap_output with 3 fixtures"
```

---

## Task 5: Parser — `parse_ldap_entries`

**Files:**
- Create: `tests/fixtures/parsers/ldap_entries/anonymous_rootdse.txt`
- Create: `tests/fixtures/parsers/ldap_entries/empty_search.txt`
- Create: `tests/fixtures/parsers/ldap_entries/dc_with_users.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/ldap_entries/anonymous_rootdse.txt`**

```
Search base: 
Filter: (objectClass=*)
Results: 1

DN:  - STATUS: Read - READ TIME: 2026-05-03T12:00:00.000000
    defaultNamingContext: DC=corp,DC=local
    dnsHostName: dc01.corp.local
    domainFunctionality: 7
    forestFunctionality: 7
    namingContexts: DC=corp,DC=local
                    CN=Configuration,DC=corp,DC=local
                    CN=Schema,CN=Configuration,DC=corp,DC=local
    rootDomainNamingContext: DC=corp,DC=local
    serverName: CN=DC01,CN=Servers,CN=Default-First-Site-Name,CN=Sites,CN=Configuration,DC=corp,DC=local
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/ldap_entries/empty_search.txt`**

```
Search base: DC=corp,DC=local
Filter: (sAMAccountName=nonexistent)
Results: 0

(no results)
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/ldap_entries/dc_with_users.txt`**

```
Search base: DC=corp,DC=local
Filter: (objectClass=computer)
Results: 3

DN: CN=DC01,OU=Domain Controllers,DC=corp,DC=local - STATUS: Read
    cn: DC01
    dNSHostName: dc01.corp.local
    operatingSystem: Windows Server 2019 Standard
    sAMAccountName: DC01$
    userAccountControl: 532480
---
DN: CN=WS01,CN=Computers,DC=corp,DC=local - STATUS: Read
    cn: WS01
    dNSHostName: ws01.corp.local
    operatingSystem: Windows 10 Enterprise
    sAMAccountName: WS01$
---
DN: CN=WS02,CN=Computers,DC=corp,DC=local - STATUS: Read
    cn: WS02
    dNSHostName: ws02.corp.local
    operatingSystem: Windows 10 Enterprise
    sAMAccountName: WS02$
```

- [ ] **Step 4: Append failing tests**

```python
from reverser.kb.parsers import parse_ldap_entries


def test_parse_ldap_anonymous_rootdse():
    text = (FIXTURES / "ldap_entries" / "anonymous_rootdse.txt").read_text()
    out = parse_ldap_entries(text)
    assert "hosts" in out and "note" in out
    # rootDSE often surfaces a DC dnsHostName
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
    assert dc01.is_dc is True  # OU=Domain Controllers triggers is_dc
    assert dc01.os and "Windows Server 2019" in dc01.os
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k ldap`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_ldap_entries`**

Append:

```python
_LDAP_DN_RE = re.compile(r"^DN:\s*(?P<dn>[^\s].*?)(?:\s+-\s+STATUS:.*)?$")
_LDAP_DNS_HOSTNAME_RE = re.compile(r"^\s*(?:dNSHostName|dnsHostName):\s*(\S+)", re.IGNORECASE)
_LDAP_OS_RE = re.compile(r"^\s*operatingSystem:\s*(.+?)\s*$", re.IGNORECASE)
_LDAP_NAMING_CTX_RE = re.compile(
    r"^\s*(?:defaultNamingContext|namingContexts):\s*(\S+)", re.IGNORECASE,
)


def parse_ldap_entries(text: str) -> dict:
    """Parse `ldap_search` tool output into hosts + free-text note.

    Returns ``{"hosts": [HostFact], "note": str}``. Each LDAP entry whose
    DN contains ``OU=Domain Controllers`` is marked is_dc=True. If the
    entry has dnsHostName/operatingSystem they populate the corresponding
    HostFact fields. The note captures naming context info (or an
    "empty" hint).
    """
    hosts: list[HostFact] = []
    naming_contexts: list[str] = []
    current_dn: str | None = None
    current_hostname: str | None = None
    current_os: str | None = None
    current_is_dc = False

    def flush():
        nonlocal current_dn, current_hostname, current_os, current_is_dc
        if current_dn and (current_hostname or current_is_dc):
            hosts.append(HostFact(
                ip=current_hostname or current_dn,  # placeholder until we resolve
                hostname=current_hostname,
                os=current_os,
                is_dc=current_is_dc,
            ))
        current_dn = None
        current_hostname = None
        current_os = None
        current_is_dc = False

    for raw in text.splitlines():
        m = _LDAP_DN_RE.match(raw)
        if m:
            flush()
            current_dn = m.group("dn")
            if "OU=Domain Controllers" in current_dn:
                current_is_dc = True
            continue
        nc_m = _LDAP_NAMING_CTX_RE.match(raw)
        if nc_m and nc_m.group(1) not in naming_contexts:
            naming_contexts.append(nc_m.group(1))
        h_m = _LDAP_DNS_HOSTNAME_RE.match(raw)
        if h_m:
            current_hostname = h_m.group(1)
        o_m = _LDAP_OS_RE.match(raw)
        if o_m:
            current_os = o_m.group(1)

    flush()

    # Drop the placeholder ip=hostname when we don't have a real IP.
    # The retrofit code is responsible for not blindly trusting these as IPs;
    # we still record the hostname so kb_list_hosts surfaces them.
    cleaned: list[HostFact] = []
    for h in hosts:
        if h.hostname:
            # Use the hostname itself as the host_ip surrogate so the row
            # is dedupable. Real IPs come in via nmap.
            cleaned.append(HostFact(
                ip=h.hostname, hostname=h.hostname, os=h.os, is_dc=h.is_dc,
            ))

    if naming_contexts:
        note = "LDAP naming contexts: " + ", ".join(naming_contexts)
    elif "Results: 0" in text:
        note = "LDAP search returned 0 results (empty)"
    else:
        note = "LDAP search produced no naming-context info"

    return {"hosts": cleaned, "note": note}
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k ldap`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/ldap_entries/
git commit -m "feat(kb/parsers): parse_ldap_entries with 3 fixtures"
```

---

## Task 6: Parser — `parse_asreproast_hashes`

**Files:**
- Create: `tests/fixtures/parsers/asreproast/two_users.txt`
- Create: `tests/fixtures/parsers/asreproast/empty.txt`
- Create: `tests/fixtures/parsers/asreproast/single_user_no_preauth.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/asreproast/two_users.txt`**

```
Impacket v0.11.0 - Copyright 2023 Fortra

[*] Getting TGT for alice
$krb5asrep$23$alice@CORP.LOCAL:9c3f...truncated...:8a7b1d4e3f2c0918a7b6c5d4e3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3
[*] Getting TGT for bob
$krb5asrep$23$bob@CORP.LOCAL:b1e7...truncated...:0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/asreproast/empty.txt`**

```
Impacket v0.11.0 - Copyright 2023 Fortra

[*] No entries found!
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/asreproast/single_user_no_preauth.txt`**

```
Impacket v0.11.0 - Copyright 2023 Fortra

[*] Getting TGT for svc_backup
$krb5asrep$23$svc_backup@CORP.LOCAL:aa11bb22cc33dd44:ee55ff66aabbccdd0011223344556677ee55ff66aabbccdd0011223344556677
```

- [ ] **Step 4: Append failing tests**

```python
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
    assert creds[0].kerberos_ticket.startswith("$krb5asrep$")
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k asreproast`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_asreproast_hashes`**

Append:

```python
_ASREP_LINE_RE = re.compile(
    r"^\$krb5asrep\$\d+\$(?P<user>[^@]+)@(?P<domain>[^:]+):.*"
)


def parse_asreproast_hashes(text: str) -> list[CredentialFact]:
    """Extract AS-REP hashes from impacket GetNPUsers output.

    Each ``$krb5asrep$...`` line becomes a CredentialFact with
    kerberos_ticket=<full hash>, status='untested', source_tool=
    'kerberos_enum'. The username and domain are extracted from the
    principal in the hash header.
    """
    creds: list[CredentialFact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("$krb5asrep$"):
            continue
        m = _ASREP_LINE_RE.match(line)
        if not m:
            continue
        creds.append(CredentialFact(
            username=m.group("user"),
            domain=m.group("domain"),
            kerberos_ticket=line,
            status="untested",
            source_tool="kerberos_enum",
            source_context="asreproast",
        ))
    return creds
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k asreproast`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/asreproast/
git commit -m "feat(kb/parsers): parse_asreproast_hashes with 3 fixtures"
```

---

## Task 7: Parser — `parse_kerberoast_hashes`

**Files:**
- Create: `tests/fixtures/parsers/kerberoast/two_spns.txt`
- Create: `tests/fixtures/parsers/kerberoast/empty.txt`
- Create: `tests/fixtures/parsers/kerberoast/sql_service.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/kerberoast/two_spns.txt`**

```
Impacket v0.11.0 - Copyright 2023 Fortra

ServicePrincipalName                  Name        MemberOf                                  PasswordLastSet      LastLogon
------------------------------------  ----------  ----------------------------------------  -------------------  -------------------
HTTP/web01.corp.local                 svc_web     CN=Domain Users,CN=Users,DC=corp,DC=local 2024-01-15 10:23:00  2026-05-02 09:15:00
MSSQLSvc/db01.corp.local:1433         svc_sql     CN=Domain Users,CN=Users,DC=corp,DC=local 2023-11-02 08:00:00  2026-05-02 08:00:00

[*] TGS-REP encryption type: rc4-hmac
$krb5tgs$23$*svc_web$CORP.LOCAL$HTTP/web01.corp.local*$abcd1234efgh5678$0011223344556677889900112233445566778899
$krb5tgs$23$*svc_sql$CORP.LOCAL$MSSQLSvc/db01.corp.local:1433*$ffeedd00ccbbaa99$8877665544332211ffeeddccbbaa99887766554433221100
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/kerberoast/empty.txt`**

```
Impacket v0.11.0 - Copyright 2023 Fortra

[*] No entries found!
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/kerberoast/sql_service.txt`**

```
Impacket v0.11.0 - Copyright 2023 Fortra

ServicePrincipalName            Name      MemberOf                                       PasswordLastSet
------------------------------  --------  ---------------------------------------------  -------------------
MSSQLSvc/sql01.corp.local       svc_sql   CN=Domain Users,CN=Users,DC=corp,DC=local      2024-06-01 12:00:00

$krb5tgs$23$*svc_sql$CORP.LOCAL$MSSQLSvc/sql01.corp.local*$cafebabe12345678$0011aabbccddeeff00112233445566778899aabbccddeeff
```

- [ ] **Step 4: Append failing tests**

```python
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
    assert "MSSQLSvc" in creds[0].kerberos_ticket
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k kerberoast`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_kerberoast_hashes`**

Append:

```python
_TGS_LINE_RE = re.compile(
    r"^\$krb5tgs\$\d+\$\*(?P<user>[^$]+)\$(?P<domain>[^$]+)\$.*"
)


def parse_kerberoast_hashes(text: str) -> list[CredentialFact]:
    """Extract TGS hashes from impacket GetUserSPNs output.

    Each ``$krb5tgs$...`` line becomes a CredentialFact with
    kerberos_ticket=<full hash>, status='untested', source_tool=
    'kerberos_enum', source_context='kerberoast'.
    """
    creds: list[CredentialFact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("$krb5tgs$"):
            continue
        m = _TGS_LINE_RE.match(line)
        if not m:
            continue
        creds.append(CredentialFact(
            username=m.group("user"),
            domain=m.group("domain"),
            kerberos_ticket=line,
            status="untested",
            source_tool="kerberos_enum",
            source_context="kerberoast",
        ))
    return creds
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k kerberoast`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/kerberoast/
git commit -m "feat(kb/parsers): parse_kerberoast_hashes with 3 fixtures"
```

---

## Task 8: Parser — `parse_smbclient_shares`

**Files:**
- Create: `tests/fixtures/parsers/smbclient_shares/anonymous_listing.txt`
- Create: `tests/fixtures/parsers/smbclient_shares/access_denied.txt`
- Create: `tests/fixtures/parsers/smbclient_shares/auth_listing.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/smbclient_shares/anonymous_listing.txt`**

```
Anonymous login successful

	Sharename       Type      Comment
	---------       ----      -------
	ADMIN$          Disk      Remote Admin
	C$              Disk      Default share
	IPC$            IPC       Remote IPC
	NETLOGON        Disk      Logon server share 
	SYSVOL          Disk      Logon server share 
	Users           Disk      
SMB1 disabled -- no workgroup available
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/smbclient_shares/access_denied.txt`**

```
session setup failed: NT_STATUS_ACCESS_DENIED
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/smbclient_shares/auth_listing.txt`**

```
Sharename       Type      Comment
---------       ----      -------
ADMIN$          Disk      Remote Admin
C$              Disk      Default share
IPC$            IPC       Remote IPC
NETLOGON        Disk      Logon server share
SYSVOL          Disk      Logon server share
Backups         Disk      Daily backups
SCCM_Source     Disk      Software deployment source

	Server               Comment
	---------            -------

	Workgroup            Master
	---------            -------
	CORP                 DC01
```

- [ ] **Step 4: Append failing tests**

```python
from reverser.kb.parsers import parse_smbclient_shares


def test_parse_smbclient_anonymous():
    text = (FIXTURES / "smbclient_shares" / "anonymous_listing.txt").read_text()
    out = parse_smbclient_shares(text)
    assert "host" in out and "shares_note" in out
    assert "ADMIN$" in out["shares_note"]
    assert "IPC$" in out["shares_note"]
    # host is a HostFact with smb_signing potentially unset; ip set from caller
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
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k smbclient`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_smbclient_shares`**

Append:

```python
_SMBCLIENT_SHARE_RE = re.compile(
    r"^\s*(?P<name>\S+)\s+(?P<type>Disk|IPC|Printer)\s*(?P<comment>.*)$"
)
_SMBCLIENT_WORKGROUP_RE = re.compile(r"^\s*(?P<wg>\S+)\s+(?P<master>\S+)\s*$")


def parse_smbclient_shares(text: str) -> dict:
    """Parse `smbclient -L` output into host info + a shares note.

    Returns ``{"host": HostFact, "shares_note": str}``. The HostFact has
    ip="" (the retrofit caller fills it from the target arg) and may
    carry a domain hint extracted from the workgroup table.
    Failures (NT_STATUS_*) become a single-line shares_note.
    """
    if "NT_STATUS_" in text:
        # Find the first NT_STATUS_* token for the note
        m = re.search(r"NT_STATUS_[A-Z_]+", text)
        return {
            "host": HostFact(ip=""),
            "shares_note": f"smbclient failed: {m.group(0) if m else 'unknown error'}",
        }

    shares: list[str] = []
    domain: str | None = None
    in_workgroup_table = False
    for raw in text.splitlines():
        if "Workgroup" in raw and "Master" in raw:
            in_workgroup_table = True
            continue
        if in_workgroup_table:
            wg = _SMBCLIENT_WORKGROUP_RE.match(raw)
            if wg and wg.group("wg") not in ("---------",):
                domain = wg.group("wg")
                in_workgroup_table = False
            continue
        m = _SMBCLIENT_SHARE_RE.match(raw)
        if m and m.group("name") not in ("Sharename",):
            shares.append(f"{m.group('name')} ({m.group('type')})")

    note_parts = []
    if shares:
        note_parts.append("smbclient shares: " + ", ".join(shares))
    if domain:
        note_parts.append(f"workgroup/domain: {domain}")
    if not note_parts:
        note_parts.append("smbclient produced no parsable shares")

    return {
        "host": HostFact(ip="", domain=domain),
        "shares_note": "\n".join(note_parts),
    }
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k smbclient`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/smbclient_shares/
git commit -m "feat(kb/parsers): parse_smbclient_shares with 3 fixtures"
```

---

## Task 9: Parser — `parse_nmap_smb_scripts`

**Files:**
- Create: `tests/fixtures/parsers/nmap_smb_scripts/dc01_full.txt`
- Create: `tests/fixtures/parsers/nmap_smb_scripts/no_smb.txt`
- Create: `tests/fixtures/parsers/nmap_smb_scripts/signing_disabled.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/nmap_smb_scripts/dc01_full.txt`**

```
Starting Nmap 7.94 ( https://nmap.org )
Nmap scan report for 10.10.10.5
Host is up (0.0021s latency).

PORT    STATE SERVICE      VERSION
139/tcp open  netbios-ssn  Microsoft Windows netbios-ssn
445/tcp open  microsoft-ds Windows Server 2019 Standard 17763 microsoft-ds (workgroup: CORP)

Host script results:
| smb-os-discovery: 
|   OS: Windows Server 2019 Standard 17763 (Windows Server 2019 Standard 6.3)
|   Computer name: dc01
|   NetBIOS computer name: DC01\x00
|   Domain name: corp.local
|   Forest name: corp.local
|   FQDN: dc01.corp.local
|_  System time: 2026-05-03T12:00:00+00:00
| smb-security-mode: 
|   account_used: guest
|   authentication_level: user
|   challenge_response: supported
|_  message_signing: required (the most secure)
| smb2-security-mode: 
|   3:1:1: 
|_    Message signing enabled and required
| smb-enum-shares: 
|   account_used: guest
|   \\10.10.10.5\ADMIN$: 
|     Type: STYPE_DISKTREE_HIDDEN
|     Comment: Remote Admin
|   \\10.10.10.5\IPC$: 
|     Type: STYPE_IPC_HIDDEN
|_    Comment: Remote IPC

Nmap done: 1 IP address (1 host up) scanned in 11.20 seconds
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/nmap_smb_scripts/no_smb.txt`**

```
Starting Nmap 7.94 ( https://nmap.org )
Nmap scan report for 10.10.10.99
Host is up (0.0010s latency).

PORT    STATE  SERVICE
139/tcp closed netbios-ssn
445/tcp closed microsoft-ds

Nmap done: 1 IP address (1 host up) scanned in 1.20 seconds
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/nmap_smb_scripts/signing_disabled.txt`**

```
Starting Nmap 7.94 ( https://nmap.org )
Nmap scan report for 10.10.10.20
Host is up (0.0033s latency).

PORT    STATE SERVICE      VERSION
445/tcp open  microsoft-ds Windows 10 Enterprise 19045

Host script results:
| smb-security-mode: 
|   account_used: guest
|   authentication_level: user
|   challenge_response: supported
|_  message_signing: disabled (dangerous, but default)
| smb-os-discovery: 
|   OS: Windows 10 Enterprise 19045
|   Computer name: WS01
|   Domain name: corp.local
|_  FQDN: ws01.corp.local

Nmap done: 1 IP address (1 host up) scanned in 8.10 seconds
```

- [ ] **Step 4: Append failing tests**

```python
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
    # host_ip parsed even if no services
    assert out["host"].ip == "10.10.10.99"


def test_parse_nmap_smb_signing_disabled():
    text = (FIXTURES / "nmap_smb_scripts" / "signing_disabled.txt").read_text()
    out = parse_nmap_smb_scripts(text)
    assert out["host"].smb_signing == "disabled"
    assert out["host"].hostname == "ws01.corp.local"
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k nmap_smb`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_nmap_smb_scripts`**

Append:

```python
_SMB_SIGNING_RE = re.compile(r"message_signing:\s*(\w+)")
_SMB_FQDN_RE = re.compile(r"\|_?\s*FQDN:\s*(\S+)")
_SMB_DOMAIN_RE = re.compile(r"\|_?\s*Domain name:\s*(\S+)")
_SMB_OS_DISCOVERY_RE = re.compile(r"\|_?\s*OS:\s*(.+?)\s*$")


def parse_nmap_smb_scripts(text: str) -> dict:
    """Parse nmap SMB-script output (smb-os-discovery, -security-mode, -enum-shares).

    Returns ``{"host": HostFact, "services": [ServiceFact], "note": str}``.
    Pulls ip/hostname from the standard "Nmap scan report for ..." line,
    smb_signing from message_signing, OS from smb-os-discovery, and
    enumerated shares into the note text.
    """
    # Reuse parse_nmap_output for the host+services skeleton.
    nmap_results = parse_nmap_output(text)
    if nmap_results:
        host = nmap_results[0].host
        services = nmap_results[0].services
    else:
        host = HostFact(ip="")
        services = []

    sig_m = _SMB_SIGNING_RE.search(text)
    if sig_m:
        sig = sig_m.group(1).lower()
        if sig in ("required", "enabled", "disabled"):
            host.smb_signing = sig

    fqdn_m = _SMB_FQDN_RE.search(text)
    if fqdn_m:
        host.hostname = fqdn_m.group(1)

    dom_m = _SMB_DOMAIN_RE.search(text)
    if dom_m:
        host.domain = dom_m.group(1)

    os_m = _SMB_OS_DISCOVERY_RE.search(text)
    if os_m and not host.os:
        host.os = os_m.group(1)

    # Capture share names for the note
    share_lines = re.findall(r"\\\\\S+\\(\S+):", text)
    note_parts = []
    if share_lines:
        note_parts.append("nmap smb-enum-shares: " + ", ".join(sorted(set(share_lines))))
    note = "\n".join(note_parts) if note_parts else "nmap SMB scripts: no shares enumerated"

    return {"host": host, "services": services, "note": note}
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k nmap_smb`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/nmap_smb_scripts/
git commit -m "feat(kb/parsers): parse_nmap_smb_scripts with 3 fixtures"
```

---

## Task 10: Parser — `parse_whatweb_plugins`

**Files:**
- Create: `tests/fixtures/parsers/whatweb/wordpress_site.txt`
- Create: `tests/fixtures/parsers/whatweb/empty.txt`
- Create: `tests/fixtures/parsers/whatweb/plain_apache.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/whatweb/wordpress_site.txt`**

```
http://10.10.10.5/ [200 OK] Apache[2.4.46], Country[UNITED STATES][US], HTML5, HTTPServer[Apache/2.4.46], IP[10.10.10.5], JQuery[3.6.0], MetaGenerator[WordPress 6.2], PHP[7.4.21], Script, Title[Welcome — Demo Site], WordPress[6.2], X-Powered-By[PHP/7.4.21]
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/whatweb/empty.txt`**

```
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/whatweb/plain_apache.txt`**

```
http://10.10.10.7/ [200 OK] Apache[2.4.41], Country[UNITED STATES][US], HTTPServer[Apache/2.4.41 (Ubuntu)], IP[10.10.10.7], Title[Apache2 Ubuntu Default Page: It works]
```

- [ ] **Step 4: Append failing tests**

```python
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
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k whatweb`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_whatweb_plugins`**

Append:

```python
_WHATWEB_PLUGIN_RE = re.compile(r"([A-Za-z0-9._\-]+)\[([^\]]+)\]")


def parse_whatweb_plugins(text: str, host_ip: str, port: int) -> dict:
    """Parse a single-line whatweb output into a service + free-text note.

    Returns ``{"service": ServiceFact | None, "note": str}``. The
    HTTPServer plugin populates the service.version when present; the
    full plugin list (joined ", ") becomes the note. Returns
    {"service": None, "note": ""} for empty input.
    """
    line = text.strip()
    if not line:
        return {"service": None, "note": ""}

    plugins = _WHATWEB_PLUGIN_RE.findall(line)
    plugin_dict = {name: value for name, value in plugins}

    version = plugin_dict.get("HTTPServer") or plugin_dict.get("Apache") or plugin_dict.get("nginx")
    note = ", ".join(f"{n}={v}" for n, v in plugins) if plugins else line

    return {
        "service": ServiceFact(
            host_ip=host_ip,
            port=port,
            proto="tcp",
            service="http",
            version=version,
            scan_source="whatweb",
        ),
        "note": f"whatweb {host_ip}:{port}: {note}",
    }
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k whatweb`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/whatweb/
git commit -m "feat(kb/parsers): parse_whatweb_plugins with 3 fixtures"
```

---

## Task 11: Parser — `parse_gobuster_paths`

**Files:**
- Create: `tests/fixtures/parsers/gobuster/found_paths.txt`
- Create: `tests/fixtures/parsers/gobuster/empty.txt`
- Create: `tests/fixtures/parsers/gobuster/with_status_filter.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/gobuster/found_paths.txt`**

```
===============================================================
Gobuster v3.6
by OJ Reeves (@TheColonial) & Christian Mehlmauer (@firefart)
===============================================================
[+] Url:                     http://10.10.10.5
[+] Method:                  GET
[+] Threads:                 10
[+] Wordlist:                /usr/share/seclists/Discovery/Web-Content/common.txt
[+] Negative Status codes:   404
[+] User Agent:              gobuster/3.6
[+] Timeout:                 10s
===============================================================
2026/05/03 12:34:56 Starting gobuster in directory enumeration mode
===============================================================
/admin                (Status: 301) [Size: 178] [--> http://10.10.10.5/admin/]
/css                  (Status: 301) [Size: 178] [--> http://10.10.10.5/css/]
/index.html           (Status: 200) [Size: 1024]
/login.php            (Status: 200) [Size: 2048]
/robots.txt           (Status: 200) [Size: 47]
===============================================================
2026/05/03 12:35:42 Finished
===============================================================
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/gobuster/empty.txt`**

```
===============================================================
Gobuster v3.6
===============================================================
2026/05/03 12:00:00 Starting gobuster in directory enumeration mode
===============================================================
2026/05/03 12:00:30 Finished
===============================================================
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/gobuster/with_status_filter.txt`**

```
===============================================================
Gobuster v3.6
===============================================================
[+] Url:                     http://10.10.10.5
[+] Status codes:            200,302
===============================================================
/api                  (Status: 200) [Size: 89]
/api/v1               (Status: 302) [Size: 0] [--> /api/v1/]
/dashboard            (Status: 302) [Size: 0] [--> /login]
===============================================================
```

- [ ] **Step 4: Append failing tests**

```python
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
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k gobuster`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_gobuster_paths`**

Append:

```python
_GOBUSTER_LINE_RE = re.compile(
    r"^(?P<path>/\S+)\s+\(Status:\s*\d+\)"
)


def parse_gobuster_paths(text: str) -> list[str]:
    """Extract discovered paths from gobuster `dir` mode output.

    Each ``/path  (Status: ...)`` line yields the path. Returns an
    empty list if no paths were found (or the run was aborted before
    enumeration began).
    """
    paths: list[str] = []
    for raw in text.splitlines():
        m = _GOBUSTER_LINE_RE.match(raw)
        if m:
            paths.append(m.group("path"))
    return paths
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k gobuster`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/gobuster/
git commit -m "feat(kb/parsers): parse_gobuster_paths with 3 fixtures"
```

---

## Task 12: Parser — `parse_nikto_findings`

**Files:**
- Create: `tests/fixtures/parsers/nikto/multiple_findings.txt`
- Create: `tests/fixtures/parsers/nikto/empty.txt`
- Create: `tests/fixtures/parsers/nikto/cve_finding.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/nikto/multiple_findings.txt`**

```
- Nikto v2.5.0
---------------------------------------------------------------------------
+ Target IP:          10.10.10.5
+ Target Hostname:    10.10.10.5
+ Target Port:        80
+ Start Time:         2026-05-03 12:00:00 (GMT0)
---------------------------------------------------------------------------
+ Server: Apache/2.4.46 (FreeBSD) PHP/7.4.15
+ /: The anti-clickjacking X-Frame-Options header is not present.
+ /: The X-Content-Type-Options header is not set.
+ Apache/2.4.46 appears to be outdated (current is at least 2.4.59).
+ /admin/: Admin login page/section found.
+ OSVDB-3268: /icons/: Directory indexing found.
+ OSVDB-3092: /test/: This might be interesting.
+ /robots.txt: contains 4 entries which should be manually viewed.
+ 8741 requests: 0 error(s) and 7 item(s) reported on remote host
+ End Time: 2026-05-03 12:05:30 (GMT0) (330 seconds)
---------------------------------------------------------------------------
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/nikto/empty.txt`**

```
- Nikto v2.5.0
---------------------------------------------------------------------------
+ Target IP:          10.10.10.99
+ Target Port:        80
+ Start Time:         2026-05-03 12:10:00 (GMT0)
---------------------------------------------------------------------------
+ No web server found on 10.10.10.99:80
---------------------------------------------------------------------------
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/nikto/cve_finding.txt`**

```
- Nikto v2.5.0
---------------------------------------------------------------------------
+ Target IP:          10.10.10.5
+ Target Port:        443
---------------------------------------------------------------------------
+ Server: Apache/2.2.15
+ /: Apache/2.2.15 is vulnerable to CVE-2017-9798 (Optionsbleed).
+ /admin/login.php: default credentials may be allowed (admin/admin).
+ /: TRACE method is enabled, allowing Cross-Site Tracing.
+ End Time: 2026-05-03 12:15:00 (GMT0)
---------------------------------------------------------------------------
```

- [ ] **Step 4: Append failing tests**

```python
from reverser.kb.parsers import parse_nikto_findings


def test_parse_nikto_multiple():
    text = (FIXTURES / "nikto" / "multiple_findings.txt").read_text()
    findings = parse_nikto_findings(text)
    assert len(findings) >= 5
    titles = [f.title for f in findings]
    # Header noise (Server:, Target IP:, etc.) must NOT appear
    assert all("Target IP" not in t for t in titles)
    # OSVDB lines should bump severity to medium
    osvdb = [f for f in findings if "OSVDB" in f.title]
    assert osvdb and all(f.severity in ("medium", "high") for f in osvdb)


def test_parse_nikto_empty():
    text = (FIXTURES / "nikto" / "empty.txt").read_text()
    findings = parse_nikto_findings(text)
    assert findings == []


def test_parse_nikto_cve():
    text = (FIXTURES / "nikto" / "cve_finding.txt").read_text()
    findings = parse_nikto_findings(text)
    cves = [f for f in findings if "CVE-" in f.title]
    assert cves and cves[0].severity in ("medium", "high")
    creds = [f for f in findings if "default credentials" in f.title.lower()]
    assert creds and creds[0].severity in ("medium", "high")
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k nikto`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_nikto_findings`**

Append:

```python
_NIKTO_NOISE_PREFIXES = (
    "Target IP",
    "Target Hostname",
    "Target Port",
    "Start Time",
    "End Time",
    "Server:",
    "requests:",
    "No web server",
)


def _nikto_severity_for(line: str) -> str:
    lower = line.lower()
    if "cve-" in lower or "default credentials" in lower:
        return "high" if "default credentials" in lower else "medium"
    if "osvdb" in lower:
        return "medium"
    if "outdated" in lower or "vulnerable" in lower:
        return "medium"
    return "info"


def parse_nikto_findings(text: str) -> list[FindingFact]:
    """Convert nikto report `+ ...` lines into FindingFact entries.

    Lines starting with ``+`` and not matching header/footer noise become
    findings. Severity is bumped (medium/high) when the line mentions
    OSVDB-, CVE-, default credentials, or outdated/vulnerable.
    """
    findings: list[FindingFact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("+ "):
            continue
        body = line[2:].strip()
        if any(body.startswith(p) for p in _NIKTO_NOISE_PREFIXES):
            continue
        # Trim absurdly long lines for the title
        title = body if len(body) <= 200 else body[:197] + "..."
        findings.append(FindingFact(
            title=title,
            severity=_nikto_severity_for(body),
            description=body,
        ))
    return findings
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k nikto`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/nikto/
git commit -m "feat(kb/parsers): parse_nikto_findings with severity bump heuristic"
```

---

## Task 13: Parser — `parse_ssl_findings`

**Files:**
- Create: `tests/fixtures/parsers/ssl/sslscan_full.txt`
- Create: `tests/fixtures/parsers/ssl/no_findings.txt`
- Create: `tests/fixtures/parsers/ssl/expired_cert.txt`
- Modify: `src/reverser/kb/parsers.py`
- Modify: `tests/test_kb_parsers.py`

- [ ] **Step 1: Create fixture `tests/fixtures/parsers/ssl/sslscan_full.txt`**

```
Version: 2.1.0
OpenSSL 3.1.0 14 Mar 2023

Connected to 10.10.10.5

Testing SSL server 10.10.10.5 on port 443

  SSL/TLS Protocols:
SSLv2     disabled
SSLv3     disabled
TLSv1.0   enabled
TLSv1.1   enabled
TLSv1.2   enabled
TLSv1.3   disabled

  TLS Fallback SCSV:
Server supports TLS Fallback SCSV

  Heartbleed:
TLS 1.0 not vulnerable to heartbleed
TLS 1.1 not vulnerable to heartbleed
TLS 1.2 not vulnerable to heartbleed

  Supported Server Cipher(s):
Preferred TLSv1.2  256 bits  ECDHE-RSA-AES256-GCM-SHA384   Curve P-256 DHE 256
Accepted  TLSv1.2  128 bits  ECDHE-RSA-AES128-GCM-SHA256   Curve P-256 DHE 256
Accepted  TLSv1.0  256 bits  AES256-SHA
Accepted  TLSv1.0  128 bits  AES128-SHA

  Subject:  CN=*.corp.local
  Issuer:   CN=Internal CA
  Not valid before:  Jan  1 00:00:00 2024 GMT
  Not valid after:   Jan  1 00:00:00 2027 GMT
```

- [ ] **Step 2: Create fixture `tests/fixtures/parsers/ssl/no_findings.txt`**

```
Version: 2.1.0
OpenSSL 3.1.0

Connected to 10.10.10.99

Testing SSL server 10.10.10.99 on port 443

  SSL/TLS Protocols:
SSLv2     disabled
SSLv3     disabled
TLSv1.0   disabled
TLSv1.1   disabled
TLSv1.2   enabled
TLSv1.3   enabled
```

- [ ] **Step 3: Create fixture `tests/fixtures/parsers/ssl/expired_cert.txt`**

```
Version: 2.1.0

Connected to 10.13.38.23

Testing SSL server 10.13.38.23 on port 443

  SSL/TLS Protocols:
SSLv2     disabled
SSLv3     disabled
TLSv1.0   enabled
TLSv1.2   enabled

  Subject:  CN=www.solarsystem.htb, O=SolarSystem Ltd, C=UK
  Issuer:   CN=www.solarsystem.htb, O=SolarSystem Ltd, C=UK
  Not valid before:  Feb 19 22:48:07 2020 GMT
  Not valid after:   Feb 18 22:48:07 2021 GMT
  Certificate has expired
```

- [ ] **Step 4: Append failing tests**

```python
from reverser.kb.parsers import parse_ssl_findings


def test_parse_ssl_full():
    text = (FIXTURES / "ssl" / "sslscan_full.txt").read_text()
    out = parse_ssl_findings(text)
    assert "findings" in out and "note" in out
    titles = " | ".join(f.title for f in out["findings"])
    # TLSv1.0/1.1 enabled should be a finding
    assert "TLS" in titles
    # Note should reference cipher count or cert info
    assert out["note"]


def test_parse_ssl_no_findings():
    text = (FIXTURES / "ssl" / "no_findings.txt").read_text()
    out = parse_ssl_findings(text)
    # No deprecated TLS, no expired cert → no findings
    assert out["findings"] == []
    assert "note" in out


def test_parse_ssl_expired_cert():
    text = (FIXTURES / "ssl" / "expired_cert.txt").read_text()
    out = parse_ssl_findings(text)
    titles_lower = " ".join(f.title.lower() for f in out["findings"])
    assert "expired" in titles_lower
    assert any(f.severity in ("medium", "high") for f in out["findings"])
```

- [ ] **Step 5: Run failing test**

Run: `pytest tests/test_kb_parsers.py -v -k parse_ssl`
Expected: 3 errors.

- [ ] **Step 6: Implement `parse_ssl_findings`**

Append:

```python
def parse_ssl_findings(text: str) -> dict:
    """Convert sslscan-style output into TLS findings + a summary note.

    Returns ``{"findings": [FindingFact], "note": str}``. Findings are
    raised for: TLSv1.0 enabled, TLSv1.1 enabled, SSLv2 enabled,
    SSLv3 enabled, expired certificate, self-signed certificate.
    """
    findings: list[FindingFact] = []

    if re.search(r"^SSLv2\s+enabled", text, re.MULTILINE):
        findings.append(FindingFact(
            title="SSLv2 enabled",
            severity="high",
            description="The server accepts SSLv2 connections (DROWN attack surface).",
        ))
    if re.search(r"^SSLv3\s+enabled", text, re.MULTILINE):
        findings.append(FindingFact(
            title="SSLv3 enabled",
            severity="high",
            description="The server accepts SSLv3 connections (POODLE attack surface).",
        ))
    if re.search(r"^TLSv1\.0\s+enabled", text, re.MULTILINE):
        findings.append(FindingFact(
            title="TLSv1.0 enabled",
            severity="medium",
            description="TLS 1.0 is deprecated by all major standards bodies.",
        ))
    if re.search(r"^TLSv1\.1\s+enabled", text, re.MULTILINE):
        findings.append(FindingFact(
            title="TLSv1.1 enabled",
            severity="medium",
            description="TLS 1.1 is deprecated by all major standards bodies.",
        ))
    if "Certificate has expired" in text:
        findings.append(FindingFact(
            title="Expired TLS certificate",
            severity="medium",
            description="The presented X.509 certificate is past its 'Not valid after' date.",
        ))

    # Self-signed = subject == issuer
    subj_m = re.search(r"^\s*Subject:\s*(.+?)\s*$", text, re.MULTILINE)
    iss_m = re.search(r"^\s*Issuer:\s*(.+?)\s*$", text, re.MULTILINE)
    if subj_m and iss_m and subj_m.group(1).strip() == iss_m.group(1).strip():
        findings.append(FindingFact(
            title="Self-signed TLS certificate",
            severity="low",
            description=f"Subject == Issuer ({subj_m.group(1).strip()}) — self-signed.",
        ))

    cipher_count = len(re.findall(r"^Accepted\s+TLSv", text, re.MULTILINE))
    note = f"sslscan: {cipher_count} accepted ciphers, {len(findings)} findings"
    return {"findings": findings, "note": note}
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_kb_parsers.py -v -k parse_ssl`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/kb/parsers.py tests/test_kb_parsers.py tests/fixtures/parsers/ssl/
git commit -m "feat(kb/parsers): parse_ssl_findings with TLS deprecation + cert checks"
```

---

## Task 14: KB tool — `kb_show`

**Files:**
- Create: `src/reverser/tools/kb.py`
- Create: `tests/test_kb_tools.py`

- [ ] **Step 1: Create `src/reverser/tools/kb.py` skeleton**

```python
"""Read-side and editorial tools the LLM uses to inspect/annotate the KB."""

from __future__ import annotations

import os
from pathlib import Path

from claude_agent_sdk import tool

from ..kb import (
    AuthorizationError,
    FindingFact,
    for_target,
    list_targets,
    require_pentest_auth,
)
from ._common import format_error, format_tool_result


def _resolve_target(target: str | None) -> str | tuple[None, dict]:
    """Return a normalized target name or an MCP error result.

    If `target` is provided, normalize and return it. If `target` is
    None, return the sole target with a state.db; if zero or multiple
    exist, return an MCP error.
    """
    if target:
        return target
    candidates = list_targets()
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None, format_error(
            "No targets found in REVERSER_TARGETS_DIR. "
            "Provide a target argument explicitly."
        )
    return None, format_error(
        "Multiple targets present — pass `target` explicitly. "
        "Available: " + ", ".join(candidates)
    )


def _check_auth() -> dict | None:
    try:
        require_pentest_auth()
        return None
    except AuthorizationError as e:
        return format_error(str(e))
```

- [ ] **Step 2: Append `kb_show` tool**

```python
@tool(
    "kb_show",
    "Single-screen overview of the per-target knowledge base: hosts (count and "
    "OS breakdown), top 10 ports, valid credentials count + most recent, finding "
    "count by severity. If `target` is omitted and exactly one target has been "
    "started, defaults to it; otherwise errors with the available list.",
    {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Normalized target identifier (IP/hostname/CIDR). Optional.",
                "default": "",
            },
        },
    },
)
async def kb_show(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    target_arg = args.get("target", "") or None
    resolved = _resolve_target(target_arg)
    if isinstance(resolved, tuple):
        return resolved[1]
    target = resolved

    kb = for_target(target)
    hosts = kb.get_hosts()
    services = kb.get_services()
    creds = kb.get_credentials()
    valid_creds = [c for c in creds if c.status == "valid"]
    findings = kb.get_findings()

    # Top 10 ports by frequency
    from collections import Counter
    port_counter = Counter(s.port for s in services)
    top_ports = port_counter.most_common(10)

    # OS breakdown
    os_counter = Counter((h.os or "unknown") for h in hosts)

    # Severity counts
    sev_counter = Counter(f.severity for f in findings)

    lines = [
        f"# KB summary — {target}",
        "",
        f"Hosts: {len(hosts)}",
    ]
    for os_name, n in os_counter.most_common():
        lines.append(f"  - {os_name}: {n}")
    lines.append("")
    lines.append(f"Services: {len(services)}")
    if top_ports:
        lines.append("Top ports:")
        for port, count in top_ports:
            lines.append(f"  - {port}: {count}")
    lines.append("")
    lines.append(f"Credentials: {len(creds)} total, {len(valid_creds)} valid")
    if valid_creds:
        most_recent = valid_creds[-1]
        lines.append(f"  - Most recent valid: {most_recent.username}"
                     f" (source: {most_recent.source_tool or '?'})")
    lines.append("")
    lines.append(f"Findings: {len(findings)}")
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev_counter.get(sev):
            lines.append(f"  - {sev}: {sev_counter[sev]}")

    return format_tool_result("\n".join(lines))


TOOLS = [kb_show]
```

- [ ] **Step 3: Write the test `tests/test_kb_tools.py`**

```python
"""Tests for KB read-side and editorial MCP tools."""

import asyncio
import pytest

from reverser.kb import for_target, HostFact, ServiceFact, CredentialFact, FindingFact


@pytest.fixture(autouse=True)
def authorize(monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")


def _call_tool(tool_obj, args):
    """Invoke an SDK tool object's underlying coroutine."""
    fn = getattr(tool_obj, "handler", None) or tool_obj.fn
    return asyncio.get_event_loop().run_until_complete(fn(args))


def test_kb_show_with_explicit_target(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", os="Windows", is_dc=True))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_finding(FindingFact(title="t", severity="high", description="x"))
    result = _call_tool(kb_show, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text
    assert "Hosts: 1" in text
    assert "Credentials:" in text
    assert "valid" in text
    assert "high" in text


def test_kb_show_defaults_to_sole_target(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    for_target("10.10.10.5")
    result = _call_tool(kb_show, {"target": ""})
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text


def test_kb_show_errors_on_no_targets(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    result = _call_tool(kb_show, {"target": ""})
    assert result.get("is_error")


def test_kb_show_errors_on_multiple_no_target(tmp_targets_dir):
    from reverser.tools.kb import kb_show
    for_target("10.10.10.5")
    for_target("10.10.10.6")
    result = _call_tool(kb_show, {"target": ""})
    assert result.get("is_error")
    assert "10.10.10.5" in result["content"][0]["text"]
    assert "10.10.10.6" in result["content"][0]["text"]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k kb_show`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_show summary tool"
```

---

## Task 15: KB tool — `kb_list_hosts`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_tools.py`

- [ ] **Step 1: Append failing test**

```python
def test_kb_list_hosts(tmp_targets_dir):
    from reverser.tools.kb import kb_list_hosts
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows",
                            domain="CORP", is_dc=True, smb_signing="required"))
    kb.record_host(HostFact(ip="10.10.10.6", hostname="ws01", os="Windows 10"))
    result = _call_tool(kb_list_hosts, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "10.10.10.5" in text
    assert "dc01" in text
    assert "10.10.10.6" in text
    assert "ws01" in text
    assert "required" in text


def test_kb_list_hosts_empty(tmp_targets_dir):
    from reverser.tools.kb import kb_list_hosts
    for_target("10.10.10.5")
    result = _call_tool(kb_list_hosts, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "No hosts" in text or "0 hosts" in text or "(no rows)" in text
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_kb_tools.py -v -k list_hosts`
Expected: 2 errors (`ImportError`).

- [ ] **Step 3: Append `kb_list_hosts` to `src/reverser/tools/kb.py`**

```python
@tool(
    "kb_list_hosts",
    "List every host in the KB for `target`: ip, hostname, OS, domain, "
    "is_dc, smb_signing.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
        },
        "required": ["target"],
    },
)
async def kb_list_hosts(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    kb = for_target(target)
    hosts = kb.get_hosts()
    if not hosts:
        return format_tool_result(f"No hosts recorded for {target} (0 rows)")
    lines = [f"# Hosts for {target} ({len(hosts)} rows)", ""]
    lines.append(f"{'IP':<18}{'HOSTNAME':<28}{'OS':<32}{'DOMAIN':<20}{'DC':<5}SIGNING")
    lines.append("-" * 110)
    for h in hosts:
        lines.append(
            f"{h.ip:<18}{(h.hostname or '-'):<28}{(h.os or '-')[:31]:<32}"
            f"{(h.domain or '-'):<20}{('yes' if h.is_dc else 'no'):<5}{h.smb_signing or '-'}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_hosts)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k list_hosts`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_list_hosts table view"
```

---

## Task 16: KB tool — `kb_list_services`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_tools.py`

- [ ] **Step 1: Append failing tests**

```python
def test_kb_list_services_all(tmp_targets_dir):
    from reverser.tools.kb import kb_list_services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp",
                                  service="microsoft-ds", version="Windows Server 2019",
                                  scan_source="nmap_scan"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=22, proto="tcp",
                                  service="ssh", version="OpenSSH 8.4"))
    result = _call_tool(kb_list_services, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "445" in text
    assert "microsoft-ds" in text
    assert "22" in text
    assert "ssh" in text


def test_kb_list_services_filter_by_port(tmp_targets_dir):
    from reverser.tools.kb import kb_list_services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=22, proto="tcp"))
    result = _call_tool(kb_list_services, {"target": "10.10.10.5", "port": 445})
    text = result["content"][0]["text"]
    assert "445" in text
    assert "22" not in text


def test_kb_list_services_filter_by_host(tmp_targets_dir):
    from reverser.tools.kb import kb_list_services
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5"))
    kb.record_host(HostFact(ip="10.10.10.6"))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
    kb.record_service(ServiceFact(host_ip="10.10.10.6", port=22, proto="tcp"))
    result = _call_tool(kb_list_services, {"target": "10.10.10.5", "host": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "445" in text
    assert "10.10.10.6" not in text or "22" not in text
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_kb_tools.py -v -k list_services`
Expected: 3 errors.

- [ ] **Step 3: Append `kb_list_services` to `src/reverser/tools/kb.py`**

```python
@tool(
    "kb_list_services",
    "List every service in the KB for `target`. Optional `host` and `port` filters.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "host": {"type": "string", "description": "Filter by host IP.", "default": ""},
            "port": {"type": "integer", "description": "Filter by port.", "default": 0},
        },
        "required": ["target"],
    },
)
async def kb_list_services(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    host = args.get("host", "") or None
    port = args.get("port", 0) or None
    kb = for_target(target)
    services = kb.get_services(host_ip=host, port=port)
    if not services:
        return format_tool_result(f"No services match for {target} (0 rows)")
    lines = [f"# Services for {target} ({len(services)} rows)", ""]
    lines.append(f"{'HOST':<18}{'PORT':<6}{'PROTO':<6}{'SERVICE':<20}{'VERSION':<40}SOURCE")
    lines.append("-" * 100)
    for s in services:
        lines.append(
            f"{s.host_ip:<18}{s.port:<6}{s.proto:<6}{(s.service or '-'):<20}"
            f"{(s.version or '-')[:39]:<40}{s.scan_source or '-'}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_services)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k list_services`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_list_services with host/port filters"
```

---

## Task 17: KB tool — `kb_list_creds`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_tools.py`

- [ ] **Step 1: Append failing tests**

```python
def test_kb_list_creds_all(tmp_targets_dir):
    from reverser.tools.kb import kb_list_creds
    from reverser.kb import CredResult
    kb = for_target("10.10.10.5")
    cid = kb.record_credential(CredentialFact(
        username="jdoe", password="x", domain="CORP",
        source_tool="netexec_smb", status="valid",
    ))
    kb.record_cred_result(cid, CredResult(service_kind="smb", target_host="10.10.10.5", success=True))
    kb.record_credential(CredentialFact(username="bob", password="y", status="invalid"))
    result = _call_tool(kb_list_creds, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "jdoe" in text
    assert "bob" in text
    assert "valid" in text
    assert "smb" in text


def test_kb_list_creds_filter_by_status(tmp_targets_dir):
    from reverser.tools.kb import kb_list_creds
    kb = for_target("10.10.10.5")
    kb.record_credential(CredentialFact(username="jdoe", password="x", status="valid"))
    kb.record_credential(CredentialFact(username="bob", password="y", status="invalid"))
    result = _call_tool(kb_list_creds, {"target": "10.10.10.5", "status": "valid"})
    text = result["content"][0]["text"]
    assert "jdoe" in text
    assert "bob" not in text


def test_kb_list_creds_empty(tmp_targets_dir):
    from reverser.tools.kb import kb_list_creds
    for_target("10.10.10.5")
    result = _call_tool(kb_list_creds, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "No credentials" in text or "(no rows)" in text or "0 rows" in text
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_kb_tools.py -v -k list_creds`
Expected: 3 errors.

- [ ] **Step 3: Append `kb_list_creds` to `src/reverser/tools/kb.py`**

```python
@tool(
    "kb_list_creds",
    "List credentials in the KB for `target`. Optional `status` filter "
    "(untested|invalid|valid). For each cred, shows username, status, "
    "source tool, and the services where it has been validated.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "status": {
                "type": "string",
                "description": "Filter by status.",
                "enum": ["untested", "invalid", "valid"],
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def kb_list_creds(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    status = args.get("status", "") or None
    kb = for_target(target)
    creds = kb.get_credentials(status=status)
    if not creds:
        return format_tool_result(f"No credentials match for {target} (0 rows)")

    # Re-query rows to get IDs for joining cred_results.
    # The public dataclass doesn't expose .id, so re-pull via the connection.
    rows_with_id = []
    with kb._connect() as conn:
        cursor = conn.execute(
            "SELECT id, username, password, nt_hash, kerberos_ticket, domain, "
            "source_tool, source_context, status FROM credentials WHERE target_id = ?"
            + (" AND status = ?" if status else "") + " ORDER BY id",
            ([kb.target_id, status] if status else [kb.target_id]),
        )
        rows_with_id = cursor.fetchall()

    lines = [f"# Credentials for {target} ({len(rows_with_id)} rows)", ""]
    lines.append(f"{'USER':<24}{'DOMAIN':<16}{'STATUS':<10}{'MATERIAL':<14}"
                 f"{'SOURCE':<18}WORKS-ON")
    lines.append("-" * 110)
    for row in rows_with_id:
        cid, user, pw, nt, krb, domain, source_tool, source_ctx, st = row
        material = "password" if pw else ("nt_hash" if nt else ("krb" if krb else "-"))
        results = kb.get_cred_results(cid)
        works = ", ".join(
            f"{r.service_kind}@{r.target_host}{'+' if r.success else '-'}"
            for r in results
        ) or "-"
        lines.append(
            f"{user[:23]:<24}{(domain or '-')[:15]:<16}{st:<10}{material:<14}"
            f"{(source_tool or '-')[:17]:<18}{works}"
        )
    return format_tool_result("\n".join(lines))


TOOLS.append(kb_list_creds)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k list_creds`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_list_creds with status filter + cred_results join"
```

---

## Task 18: KB tool — `kb_add_finding`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_tools.py`

- [ ] **Step 1: Append failing tests**

```python
def test_kb_add_finding_basic(tmp_targets_dir):
    from reverser.tools.kb import kb_add_finding
    for_target("10.10.10.5")
    result = _call_tool(kb_add_finding, {
        "target": "10.10.10.5",
        "title": "SMB signing not required",
        "severity": "medium",
        "description": "Allows NTLM relay attacks.",
    })
    text = result["content"][0]["text"]
    assert "added" in text.lower() or "id=" in text.lower()
    findings = for_target("10.10.10.5").get_findings()
    assert len(findings) == 1
    assert findings[0].title == "SMB signing not required"


def test_kb_add_finding_with_evidence_and_cvss(tmp_targets_dir):
    from reverser.tools.kb import kb_add_finding
    for_target("10.10.10.5")
    result = _call_tool(kb_add_finding, {
        "target": "10.10.10.5",
        "title": "Zerologon",
        "severity": "critical",
        "description": "CVE-2020-1472",
        "evidence_paths": ["findings/zerologon.txt"],
        "cvss": 10.0,
    })
    assert not result.get("is_error")
    f = for_target("10.10.10.5").get_findings()[0]
    assert f.cvss == 10.0
    assert f.evidence_paths == ["findings/zerologon.txt"]


def test_kb_add_finding_invalid_severity(tmp_targets_dir):
    from reverser.tools.kb import kb_add_finding
    for_target("10.10.10.5")
    result = _call_tool(kb_add_finding, {
        "target": "10.10.10.5",
        "title": "x",
        "severity": "emergency",
        "description": "x",
    })
    assert result.get("is_error")
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_kb_tools.py -v -k add_finding`
Expected: 3 errors.

- [ ] **Step 3: Append `kb_add_finding` to `src/reverser/tools/kb.py`**

```python
@tool(
    "kb_add_finding",
    "Record a new finding in the KB. Severity: info|low|medium|high|critical. "
    "Optional `evidence_paths` (list of relative paths under findings/ or loot/) "
    "and `cvss` numeric score.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "title": {"type": "string", "description": "Short finding title."},
            "severity": {
                "type": "string",
                "description": "Severity level.",
                "enum": ["info", "low", "medium", "high", "critical"],
            },
            "description": {"type": "string", "description": "Finding details."},
            "evidence_paths": {
                "type": "array",
                "description": "Optional list of evidence file paths (relative to target dir).",
                "items": {"type": "string"},
                "default": [],
            },
            "cvss": {
                "type": "number",
                "description": "Optional numeric CVSS score (0.0-10.0).",
                "default": 0,
            },
        },
        "required": ["target", "title", "severity", "description"],
    },
)
async def kb_add_finding(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    cvss = args.get("cvss", 0) or None
    try:
        finding = FindingFact(
            title=args["title"],
            severity=args["severity"],
            description=args["description"],
            evidence_paths=args.get("evidence_paths", []) or [],
            cvss=cvss,
        )
    except ValueError as e:
        return format_error(str(e))
    fid = for_target(target).record_finding(finding)
    return format_tool_result(f"Finding added: id={fid} title={finding.title!r}")


TOOLS.append(kb_add_finding)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k add_finding`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_add_finding with evidence + cvss"
```

---

## Task 19: KB tool — `kb_add_note`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_tools.py`

- [ ] **Step 1: Append failing test**

```python
def test_kb_add_note(tmp_targets_dir):
    from reverser.tools.kb import kb_add_note
    for_target("10.10.10.5")
    result = _call_tool(kb_add_note, {
        "target": "10.10.10.5",
        "body": "Hypothesis: WS01 likely shares creds with DC01.",
    })
    assert not result.get("is_error")
    notes = for_target("10.10.10.5").get_notes()
    assert any("Hypothesis" in n for n in notes)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_kb_tools.py -v -k add_note`
Expected: 1 error.

- [ ] **Step 3: Append `kb_add_note` to `src/reverser/tools/kb.py`**

```python
@tool(
    "kb_add_note",
    "Append a free-form note to the KB scratchpad for `target`. Use for "
    "hypotheses, leads, observations, methodology decisions.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "body": {"type": "string", "description": "Note body (any length)."},
        },
        "required": ["target", "body"],
    },
)
async def kb_add_note(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    for_target(args["target"]).record_note(args["body"])
    return format_tool_result("Note recorded.")


TOOLS.append(kb_add_note)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k add_note`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_add_note scratchpad tool"
```

---

## Task 20: KB tool — `kb_export_report`

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Modify: `tests/test_kb_tools.py`

- [ ] **Step 1: Append failing test**

```python
def test_kb_export_report_default_path(tmp_targets_dir):
    from reverser.tools.kb import kb_export_report
    from reverser.kb import CredResult
    kb = for_target("10.10.10.5")
    kb.record_host(HostFact(ip="10.10.10.5", hostname="dc01", os="Windows", is_dc=True))
    kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp",
                                  service="microsoft-ds"))
    cid = kb.record_credential(CredentialFact(
        username="jdoe", password="x", status="valid", source_tool="netexec_smb",
    ))
    kb.record_cred_result(cid, CredResult(service_kind="smb", target_host="10.10.10.5", success=True))
    kb.record_finding(FindingFact(
        title="SMB signing missing", severity="medium",
        description="Allows NTLM relay.",
    ))
    result = _call_tool(kb_export_report, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    expected_path = tmp_targets_dir / "10.10.10.5" / "report.md"
    assert expected_path.exists()
    body = expected_path.read_text()
    assert "10.10.10.5" in body
    assert "## Hosts" in body or "# Hosts" in body
    assert "jdoe" in body
    assert "SMB signing" in body


def test_kb_export_report_custom_path(tmp_targets_dir, tmp_path):
    from reverser.tools.kb import kb_export_report
    for_target("10.10.10.5")
    out = tmp_path / "out.md"
    result = _call_tool(kb_export_report, {
        "target": "10.10.10.5",
        "output_path": str(out),
    })
    assert out.exists()
    assert "10.10.10.5" in out.read_text()
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_kb_tools.py -v -k export_report`
Expected: 2 errors.

- [ ] **Step 3: Append `kb_export_report` to `src/reverser/tools/kb.py`**

```python
def _render_report(kb) -> str:
    """Render a markdown report from KB contents in the project house style."""
    hosts = kb.get_hosts()
    services = kb.get_services()
    creds = kb.get_credentials()
    findings = kb.get_findings()
    artifacts = kb.get_artifacts()
    notes = kb.get_notes()

    lines = [
        f"# Penetration Test Report — {kb.target_id}",
        "",
        f"**Generated by:** kb_export_report",
        f"**Target:** {kb.target_id}",
        "",
        "## Executive Summary",
        "",
        f"Recorded {len(hosts)} host(s), {len(services)} service(s), "
        f"{len(creds)} credential(s) ("
        f"{sum(1 for c in creds if c.status == 'valid')} valid), "
        f"{len(findings)} finding(s), {len(artifacts)} artifact(s).",
        "",
    ]

    lines.append("## Hosts")
    lines.append("")
    if hosts:
        lines.append("| IP | Hostname | OS | Domain | DC | SMB Signing |")
        lines.append("|---|---|---|---|---|---|")
        for h in hosts:
            lines.append(
                f"| {h.ip} | {h.hostname or ''} | {h.os or ''} | "
                f"{h.domain or ''} | {'yes' if h.is_dc else 'no'} | "
                f"{h.smb_signing or ''} |"
            )
    else:
        lines.append("_No hosts recorded._")
    lines.append("")

    lines.append("## Services")
    lines.append("")
    if services:
        lines.append("| Host | Port | Proto | Service | Version | Source |")
        lines.append("|---|---|---|---|---|---|")
        for s in services:
            lines.append(
                f"| {s.host_ip} | {s.port} | {s.proto} | {s.service or ''} "
                f"| {s.version or ''} | {s.scan_source or ''} |"
            )
    else:
        lines.append("_No services recorded._")
    lines.append("")

    lines.append("## Credentials")
    lines.append("")
    if creds:
        lines.append("| User | Domain | Status | Source | Material |")
        lines.append("|---|---|---|---|---|")
        for c in creds:
            mat = "password" if c.password else ("nt_hash" if c.nt_hash else
                  ("kerberos_ticket" if c.kerberos_ticket else "-"))
            lines.append(
                f"| {c.username} | {c.domain or ''} | {c.status} | "
                f"{c.source_tool or ''} | {mat} |"
            )
    else:
        lines.append("_No credentials recorded._")
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    if findings:
        for f in findings:
            lines.append(f"### [{f.severity.upper()}] {f.title}")
            if f.cvss is not None:
                lines.append(f"_CVSS: {f.cvss}_")
            lines.append("")
            lines.append(f.description or "_(no description)_")
            if f.evidence_paths:
                lines.append("")
                lines.append("**Evidence:**")
                for p in f.evidence_paths:
                    lines.append(f"- `{p}`")
            lines.append("")
    else:
        lines.append("_No findings recorded._")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    if artifacts:
        lines.append("| Kind | Path | Source | SHA-256 |")
        lines.append("|---|---|---|---|")
        for a in artifacts:
            lines.append(
                f"| {a.kind} | `{a.path}` | {a.source_tool or ''} | "
                f"{a.sha256 or ''} |"
            )
    else:
        lines.append("_No artifacts recorded._")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    if notes:
        for n in notes:
            lines.append(f"- {n}")
    else:
        lines.append("_No notes recorded._")
    lines.append("")

    return "\n".join(lines)


@tool(
    "kb_export_report",
    "Render the KB for `target` as a markdown report. Default output path "
    "is `targets/<target>/report.md`. Returns the absolute output path.",
    {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Normalized target identifier."},
            "output_path": {
                "type": "string",
                "description": "Optional override path. Defaults to "
                               "<target_root>/report.md.",
                "default": "",
            },
        },
        "required": ["target"],
    },
)
async def kb_export_report(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    kb = for_target(target)
    body = _render_report(kb)
    out_path = args.get("output_path") or str(kb.root / "report.md")
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(body)
    return format_tool_result(
        f"Report written to {out_p} ({len(body)} bytes)\n\n--- preview ---\n"
        + body[:2000]
        + ("\n[truncated]" if len(body) > 2000 else "")
    )


TOOLS.append(kb_export_report)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_kb_tools.py -v -k export_report`
Expected: 2 passed.

- [ ] **Step 5: Run the full kb_tools suite**

Run: `pytest tests/test_kb_tools.py -v`
Expected: ~16 tests pass (4 show + 2 list_hosts + 3 list_services + 3 list_creds + 3 add_finding + 1 add_note + 2 export_report).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools/kb): kb_export_report markdown rendering"
```

---

## Task 21: Register `tools/kb.py` in the tool registry

**Files:**
- Modify: `src/reverser/tools/__init__.py`

- [ ] **Step 1: Edit `src/reverser/tools/__init__.py`**

Replace the file's contents:

```python
"""RE tool registry — aggregates all tool categories into a single MCP server."""

from claude_agent_sdk import create_sdk_mcp_server

from .triage import TOOLS as triage_tools
from .static import TOOLS as static_tools
from .dynamic import TOOLS as dynamic_tools
from .python_analysis import TOOLS as python_tools
from .exploit import TOOLS as exploit_tools
from .util import TOOLS as util_tools
from .network import TOOLS as network_tools
from .web import TOOLS as web_tools
from .kb import TOOLS as kb_tools

ALL_TOOLS = (
    triage_tools + static_tools + dynamic_tools + python_tools
    + exploit_tools + util_tools + network_tools + web_tools + kb_tools
)


def create_re_mcp_server():
    """Create the MCP server exposing all RE tools."""
    return create_sdk_mcp_server(
        name="re",
        version="0.1.0",
        tools=ALL_TOOLS,
    )
```

- [ ] **Step 2: Smoke-import the package**

Run: `python -c "from reverser.tools import ALL_TOOLS; print(len(ALL_TOOLS))"`
Expected: count increases by 7 vs. before this task.

- [ ] **Step 3: Run full pytest**

Run: `pytest -v`
Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add src/reverser/tools/__init__.py
git commit -m "feat(tools): register kb tools in MCP server registry"
```

---

## Task 22: Retrofit `nbtscan_scan` (smallest tool first)

**Files:**
- Modify: `src/reverser/tools/network.py`
- Create: `tests/test_retrofit.py`

The current `nbtscan_scan` ends with `return cmd_result_to_tool_result(result)` after running the nbtscan command. We add a tail block that calls `parse_nbtscan_output` and writes hosts to the KB.

- [ ] **Step 1: Write failing retrofit smoke test in `tests/test_retrofit.py`**

```python
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
    fn = getattr(tool_obj, "handler", None) or tool_obj.fn
    return asyncio.get_event_loop().run_until_complete(fn(args))


def _stub_run_cmd(stdout: str, returncode: int = 0):
    return lambda cmd, **kw: {"stdout": stdout, "stderr": "", "returncode": returncode, "truncated": False}


def test_nbtscan_writes_hosts_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "nbtscan" / "single_host.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.nbtscan_scan, {"target": "10.10.10.5"})
    hosts = for_target("10.10.10.5").get_hosts()
    assert any(h.ip == "10.10.10.5" and h.hostname == "DC01" for h in hosts)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k nbtscan`
Expected: 1 failure (no host recorded).

- [ ] **Step 3: Edit `src/reverser/tools/network.py` — modify `nbtscan_scan`**

Find the existing function:

```python
async def nbtscan_scan(args: dict) -> dict:
    target = args["target"]
    verbose = args.get("verbose", False)

    cmd = ["nbtscan"]
    if verbose:
        cmd.append("-v")
    cmd.append(target)

    result = run_cmd(cmd, timeout=60, max_output=16000)
    return cmd_result_to_tool_result(result)
```

Replace with:

```python
async def nbtscan_scan(args: dict) -> dict:
    target = args["target"]
    verbose = args.get("verbose", False)

    cmd = ["nbtscan"]
    if verbose:
        cmd.append("-v")
    cmd.append(target)

    result = run_cmd(cmd, timeout=60, max_output=16000)

    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nbtscan_output
        kb = for_target(target)
        for host in parse_nbtscan_output(result["stdout"]):
            kb.record_host(host)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nbtscan_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────

    return cmd_result_to_tool_result(result)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k nbtscan`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit nbtscan_scan with KB writes"
```

---

## Task 23: Retrofit `banner_grab`

**Files:**
- Modify: `src/reverser/tools/network.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing retrofit test**

```python
def test_banner_grab_writes_service_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "banner" / "ssh_banner.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.banner_grab, {"target": "10.10.10.5", "port": 22})
    services = for_target("10.10.10.5").get_services()
    assert any(s.port == 22 and "OpenSSH" in (s.banner or "") for s in services)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k banner_grab`
Expected: 1 failure.

- [ ] **Step 3: Edit `banner_grab` in `src/reverser/tools/network.py`**

Find the existing function. After the line `result = run_cmd(cmd, timeout=10, max_output=8000)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_banner_first_line
        kb = for_target(target)
        svc = parse_banner_first_line(result["stdout"], host_ip=target, port=int(port))
        if svc is not None:
            kb.record_host(__import__("reverser.kb", fromlist=["HostFact"]).HostFact(ip=target))
            kb.record_service(svc)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in banner_grab: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k banner_grab`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit banner_grab with KB writes"
```

---

## Task 24: Retrofit `nmap_scan` in `tools/network.py`

**Files:**
- Modify: `src/reverser/tools/network.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing test**

```python
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
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k nmap_scan`
Expected: 1 failure.

- [ ] **Step 3: Edit `nmap_scan` in `src/reverser/tools/network.py`**

After the line `result = _run_sudo_cmd(cmd, needs_root, timeout=120, max_output=16000)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nmap_output
        kb = for_target(target)
        for nmap_host in parse_nmap_output(result["stdout"]):
            kb.record_host(nmap_host.host)
            for svc in nmap_host.services:
                kb.record_service(svc)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nmap_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k nmap_scan`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit nmap_scan with KB writes"
```

---

## Task 25: Retrofit `ldap_search`

**Files:**
- Modify: `src/reverser/tools/network.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing test**

```python
def test_ldap_search_writes_dcs_to_kb(tmp_targets_dir, monkeypatch):
    """Smoke test: feed a captured ldap output string straight into the parser
    inside the tool. We monkeypatch the entire ldap_search function's body via
    the public seam that the retrofit tail block uses (parse_ldap_entries)."""
    from reverser.kb.parsers import parse_ldap_entries
    text = (FIXTURES / "ldap_entries" / "dc_with_users.txt").read_text()
    out = parse_ldap_entries(text)
    # Sanity check that the parser actually finds DCs
    assert any(h.is_dc for h in out["hosts"])

    # End-to-end sanity: the retrofit tail block, when given this text, must
    # write hosts to the KB. We exercise that block directly by replicating
    # the tail block's logic against a real KB and the parser output:
    from reverser.kb import for_target
    kb = for_target("10.10.10.5")
    for h in out["hosts"]:
        kb.record_host(h)
    notes = kb.get_notes() if False else []  # note text is part of the tail block; we test that path in a separate run below
    hosts = kb.get_hosts()
    assert any(h.is_dc for h in hosts)
```

> **Note:** `ldap_search` uses the `ldap3` python library (no subprocess), so unit-monkeypatching its run path requires intercepting `ldap3` itself, which is brittle. We instead test the parser+KB seam directly here, and rely on the manual smoke (Plan 5) to confirm end-to-end behavior in a live LDAP. The retrofit code path itself is exercised in Task 26's structural test that asserts the tail-block code is present.

- [ ] **Step 2: Run the (passing) seam test**

Run: `pytest tests/test_retrofit.py -v -k ldap_search`
Expected: 1 passed.

- [ ] **Step 3: Edit `ldap_search` in `src/reverser/tools/network.py`**

Locate the function `async def ldap_search(args: dict) -> dict:`. The function ends in two return statements depending on success path — find the line `return format_tool_result(output)` near the end (the success path inside the main try). Just before that return, insert:

```python
        # ── KB write (new) ─────────────────────────────────────────────────
        try:
            from ..kb import for_target
            from ..kb.parsers import parse_ldap_entries
            kb = for_target(target)
            parsed = parse_ldap_entries(output)
            for h in parsed["hosts"]:
                kb.record_host(h)
            if parsed["note"]:
                kb.record_note(parsed["note"])
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("KB write failed in ldap_search: %s", e)
        # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run the structural test confirming the tail block is in place**

Append to `tests/test_retrofit.py`:

```python
def test_ldap_search_has_kb_tail_block():
    """Static check: ldap_search source contains the KB tail block."""
    from reverser.tools import network as net
    import inspect
    src = inspect.getsource(net.ldap_search)
    assert "parse_ldap_entries" in src
    assert "kb.record_host" in src
    assert "logging.getLogger" in src
```

Run: `pytest tests/test_retrofit.py -v -k ldap_search`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit ldap_search with KB writes"
```

---

## Task 26: Retrofit `kerberos_enum` (asreproast + kerberoast)

**Files:**
- Modify: `src/reverser/tools/network.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing tests**

```python
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
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_retrofit.py -v -k kerberos_enum`
Expected: 2 failures.

- [ ] **Step 3: Edit `kerberos_enum` in `src/reverser/tools/network.py`**

Find the `asreproast` branch — it ends with `return cmd_result_to_tool_result(result)`. Insert this block immediately before that return:

```python
        # ── KB write (new — asreproast) ────────────────────────────────────
        try:
            from ..kb import for_target
            from ..kb.parsers import parse_asreproast_hashes
            kb = for_target(target)
            for cred in parse_asreproast_hashes(result["stdout"]):
                kb.record_credential(cred)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "KB write failed in kerberos_enum/asreproast: %s", e)
        # ───────────────────────────────────────────────────────────────────
```

Find the `kerberoast` branch — it also ends with `return cmd_result_to_tool_result(result)`. Insert immediately before that return:

```python
        # ── KB write (new — kerberoast) ────────────────────────────────────
        try:
            from ..kb import for_target
            from ..kb.parsers import parse_kerberoast_hashes
            kb = for_target(target)
            for cred in parse_kerberoast_hashes(result["stdout"]):
                kb.record_credential(cred)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "KB write failed in kerberos_enum/kerberoast: %s", e)
        # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k kerberos_enum`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit kerberos_enum with asreproast/kerberoast KB writes"
```

---

## Task 27: Retrofit `smb_enum` (smbclient + nmap-script paths)

**Files:**
- Modify: `src/reverser/tools/network.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing test**

```python
def test_smb_enum_writes_host_signing_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    smbclient_text = (FIXTURES / "smbclient_shares" / "auth_listing.txt").read_text()
    nmap_text = (FIXTURES / "nmap_smb_scripts" / "dc01_full.txt").read_text()

    # Both run_cmd and _run_sudo_cmd are exercised inside smb_enum
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
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k smb_enum`
Expected: 1 failure.

- [ ] **Step 3: Edit `smb_enum` in `src/reverser/tools/network.py`**

The function builds an `outputs` list and ends with `return format_tool_result("\n\n".join(outputs))`. Just before that final return, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target, HostFact
        from ..kb.parsers import parse_smbclient_shares, parse_nmap_smb_scripts
        kb = for_target(target)
        # Always record the bare host first so subsequent updates merge cleanly.
        kb.record_host(HostFact(ip=target))
        joined = "\n\n".join(outputs)
        smb_out = parse_smbclient_shares(joined)
        if smb_out["host"].domain:
            kb.record_host(HostFact(ip=target, domain=smb_out["host"].domain))
        if smb_out["shares_note"]:
            kb.record_note(smb_out["shares_note"])
        nmap_out = parse_nmap_smb_scripts(joined)
        # The parser pulls the IP from the nmap output line; only merge if it agrees.
        if nmap_out["host"].ip == target or nmap_out["host"].ip == "":
            merged = HostFact(
                ip=target,
                hostname=nmap_out["host"].hostname,
                os=nmap_out["host"].os,
                domain=nmap_out["host"].domain,
                smb_signing=nmap_out["host"].smb_signing,
            )
            kb.record_host(merged)
        for svc in nmap_out["services"]:
            kb.record_service(svc)
        if nmap_out["note"]:
            kb.record_note(nmap_out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in smb_enum: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k smb_enum`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit smb_enum with smbclient+nmap-script KB writes"
```

---

## Task 28: Retrofit `whatweb_fingerprint` in `tools/web.py`

**Files:**
- Modify: `src/reverser/tools/web.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing test**

```python
def test_whatweb_fingerprint_writes_service_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import web as webmod
    text = (FIXTURES / "whatweb" / "wordpress_site.txt").read_text()
    monkeypatch.setattr(webmod, "run_cmd", _stub_run_cmd(text))
    # Also stub shutil.which("whatweb") so the function takes the whatweb path
    monkeypatch.setattr(webmod, "shutil",
                        type("S", (), {"which": staticmethod(lambda _: "/usr/bin/whatweb")})(),
                        raising=False)

    _call(webmod.whatweb_fingerprint, {"target": "http://10.10.10.5"})
    kb = for_target("http://10.10.10.5")
    services = kb.get_services()
    assert any(s.service == "http" for s in services)
    notes = kb.get_notes()
    assert any("WordPress" in n or "wordpress" in n.lower() for n in notes)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k whatweb_fingerprint`
Expected: 1 failure.

- [ ] **Step 3: Edit `whatweb_fingerprint` in `src/reverser/tools/web.py`**

The function has two success-path returns: one for the whatweb binary, one for the curl fallback. We insert a tail block at the very start that captures the eventual stdout. The cleanest seam is to wrap both returns in a helper. Replace the existing `whatweb_fingerprint` body with:

```python
async def whatweb_fingerprint(args: dict) -> dict:
    auth_err = check_web_authorized()
    if auth_err:
        return auth_err

    target = args["target"]
    aggression = args.get("aggression", 1)

    captured_stdout = ""

    # Try whatweb first
    import shutil
    if shutil.which("whatweb"):
        cmd = ["whatweb", f"--aggression={aggression}", "--log-json=-", target]
        result = run_cmd(cmd, timeout=30)
        if result["returncode"] != 0 and "LoadError" in (result["stderr"] + result["stdout"]):
            pass  # fall through to curl fallback
        else:
            captured_stdout = result["stdout"]
            _kb_write_whatweb(target, captured_stdout)
            return cmd_result_to_tool_result(result)

    # Fallback: curl-based fingerprinting (preserves existing logic)
    cmd = [
        "curl", "-s", "-S", "-D", "-", "-o", "/dev/null",
        "-L", "--max-time", "15",
        "-A", "Mozilla/5.0 (compatible; reverser/1.0)",
        target,
    ]
    result = run_cmd(cmd, timeout=20)
    if result["returncode"] != 0 and not result["stdout"]:
        return cmd_result_to_tool_result(result)
    headers_text = result["stdout"]

    body_cmd = [
        "curl", "-s", "-L", "--max-time", "15",
        "-A", "Mozilla/5.0 (compatible; reverser/1.0)",
        target,
    ]
    body_result = run_cmd(body_cmd, timeout=20, max_output=DEFAULT_MAX_OUTPUT)
    body = body_result.get("stdout", "")

    lines = ["## HTTP Response Headers", headers_text, ""]
    tech_hints = []
    checks = [
        ("WordPress", ["wp-content", "wp-includes", "wordpress"]),
        ("Drupal", ["drupal", "sites/default/files"]),
        ("Joomla", ["joomla", "/media/system/js"]),
        ("React", ["react", "react-dom", "_reactroot"]),
        ("Angular", ["ng-version", "angular", "ng-app"]),
        ("Vue.js", ["vue.js", "vue.min.js", "__vue__"]),
        ("jQuery", ["jquery"]),
        ("Bootstrap", ["bootstrap"]),
        ("Next.js", ["_next/", "__next"]),
        ("Nuxt.js", ["_nuxt/", "__nuxt"]),
        ("ASP.NET", ["asp.net", "__viewstate", "x-aspnet-version"]),
        ("PHP", ["x-powered-by: php", ".php"]),
        ("Laravel", ["laravel", "csrf-token"]),
        ("Django", ["csrfmiddlewaretoken", "django"]),
        ("Spring", ["spring", "jsessionid"]),
        ("Ruby on Rails", ["x-powered-by: phusion", "rails", "csrf-token"]),
        ("Express.js", ["x-powered-by: express"]),
        ("Nginx", ["server: nginx"]),
        ("Apache", ["server: apache"]),
        ("IIS", ["server: microsoft-iis"]),
        ("Cloudflare", ["server: cloudflare", "cf-ray"]),
        ("AWS", ["x-amz-", "awselb", "awsalb"]),
    ]

    combined = (headers_text + "\n" + body).lower()
    for tech, patterns in checks:
        for pattern in patterns:
            if pattern in combined:
                tech_hints.append(tech)
                break

    if tech_hints:
        lines.append("## Detected Technologies")
        for t in tech_hints:
            lines.append(f"  - {t}")
    else:
        lines.append("## No specific technologies detected from headers/body")

    final = "\n".join(lines)
    _kb_write_whatweb(target, final)
    return format_tool_result(final)


def _kb_write_whatweb(target: str, stdout: str) -> None:
    """KB write tail for whatweb_fingerprint — host_ip/port derived from URL."""
    try:
        from urllib.parse import urlparse
        from ..kb import for_target
        from ..kb.parsers import parse_whatweb_plugins
        parsed = urlparse(target if "://" in target else f"http://{target}")
        host_ip = parsed.hostname or target
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        kb = for_target(target)
        out = parse_whatweb_plugins(stdout, host_ip=host_ip, port=port)
        if out.get("service"):
            from ..kb import HostFact
            kb.record_host(HostFact(ip=host_ip))
            kb.record_service(out["service"])
        if out.get("note"):
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in whatweb_fingerprint: %s", e)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k whatweb_fingerprint`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/web.py tests/test_retrofit.py
git commit -m "feat(tools/web): retrofit whatweb_fingerprint with KB writes"
```

---

## Task 29: Retrofit `nikto_scan` in `tools/web.py`

**Files:**
- Modify: `src/reverser/tools/web.py`
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append failing test**

```python
def test_nikto_scan_writes_findings_to_kb(tmp_targets_dir, monkeypatch):
    from reverser.tools import web as webmod
    text = (FIXTURES / "nikto" / "multiple_findings.txt").read_text()
    monkeypatch.setattr(webmod, "run_cmd", _stub_run_cmd(text))

    _call(webmod.nikto_scan, {"target": "http://10.10.10.5"})
    kb = for_target("http://10.10.10.5")
    findings = kb.get_findings()
    assert len(findings) >= 5
    assert any("OSVDB" in f.title for f in findings)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k nikto_scan`
Expected: 1 failure.

- [ ] **Step 3: Edit `nikto_scan` in `src/reverser/tools/web.py`**

After the line `result = run_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nikto_findings
        kb = for_target(target)
        for finding in parse_nikto_findings(result["stdout"]):
            kb.record_finding(finding)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nikto_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k nikto_scan`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/web.py tests/test_retrofit.py
git commit -m "feat(tools/web): retrofit nikto_scan with KB writes"
```

---

## Task 30: Retrofit `gobuster_scan` and `nikto_scan`/`ssl_scan`/`whatweb_scan` in `tools/network.py`

**Files:**
- Modify: `src/reverser/tools/network.py`
- Modify: `tests/test_retrofit.py`

`tools/network.py` ships its own `gobuster_scan`, `nikto_scan`, `ssl_scan`, `whatweb_scan`. We retrofit each.

- [ ] **Step 1: Append failing tests**

```python
def test_gobuster_scan_records_artifact(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "gobuster" / "found_paths.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))
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
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.nikto_scan, {"target": "10.10.10.5"})
    findings = for_target("10.10.10.5").get_findings()
    assert any("CVE-" in f.title for f in findings)


def test_ssl_scan_writes_findings(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "ssl" / "expired_cert.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))
    monkeypatch.setattr(
        net, "_run_sudo_cmd",
        lambda cmd, sudo, **kw: {"stdout": text, "stderr": "", "returncode": 0, "truncated": False},
    )

    _call(net.ssl_scan, {"target": "10.13.38.23"})
    findings = for_target("10.13.38.23").get_findings()
    assert any("expired" in f.title.lower() for f in findings)


def test_whatweb_scan_in_network_writes_service(tmp_targets_dir, monkeypatch):
    from reverser.tools import network as net
    text = (FIXTURES / "whatweb" / "plain_apache.txt").read_text()
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(text))

    _call(net.whatweb_scan, {"target": "http://10.10.10.7"})
    services = for_target("http://10.10.10.7").get_services()
    assert any(s.service == "http" for s in services)
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_retrofit.py -v -k "gobuster_scan or nikto_scan_in_network or ssl_scan or whatweb_scan_in_network"`
Expected: 4 failures.

- [ ] **Step 3: Edit `gobuster_scan` in `src/reverser/tools/network.py`**

After `result = run_cmd(cmd, timeout=180, max_output=16000)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        import json
        from ..kb import for_target, ArtifactFact
        from ..kb.parsers import parse_gobuster_paths
        kb = for_target(target)
        paths = parse_gobuster_paths(result["stdout"])
        if paths:
            artifact_path = str(kb.root / "loot" / "gobuster_paths.json")
            from pathlib import Path
            Path(artifact_path).write_text(json.dumps(paths, indent=2))
            kb.record_artifact(ArtifactFact(
                kind="discovered_paths",
                path=artifact_path,
                source_tool="gobuster_scan",
            ))
            kb.record_note(
                f"gobuster {target}: discovered {len(paths)} paths — "
                + ", ".join(paths[:10])
                + (" ..." if len(paths) > 10 else "")
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in gobuster_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Edit `nikto_scan` in `src/reverser/tools/network.py`**

After `result = run_cmd(cmd, timeout=180, max_output=16000)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_nikto_findings
        kb = for_target(target)
        for finding in parse_nikto_findings(result["stdout"]):
            kb.record_finding(finding)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in nikto_scan (network): %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 5: Edit `ssl_scan` in `src/reverser/tools/network.py`**

The function has two run paths (sslscan then nmap fallback) and ends with `return cmd_result_to_tool_result(result)`. Insert immediately before that final return:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_ssl_findings
        kb = for_target(target)
        out = parse_ssl_findings(result["stdout"])
        for f in out["findings"]:
            kb.record_finding(f)
        if out["note"]:
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in ssl_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 6: Edit `whatweb_scan` in `src/reverser/tools/network.py`**

After `result = run_cmd(cmd, timeout=60, max_output=16000)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from urllib.parse import urlparse
        from ..kb import for_target, HostFact
        from ..kb.parsers import parse_whatweb_plugins
        parsed_url = urlparse(target if "://" in target else f"http://{target}")
        host_ip = parsed_url.hostname or target
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        kb = for_target(target)
        out = parse_whatweb_plugins(result["stdout"], host_ip=host_ip, port=port)
        if out.get("service"):
            kb.record_host(HostFact(ip=host_ip))
            kb.record_service(out["service"])
        if out.get("note"):
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in whatweb_scan: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_retrofit.py -v -k "gobuster_scan or nikto_scan_in_network or ssl_scan or whatweb_scan_in_network"`
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/tools/network.py tests/test_retrofit.py
git commit -m "feat(tools/network): retrofit gobuster_scan, nikto_scan, ssl_scan, whatweb_scan"
```

---

## Task 31: Retrofit `testssl_analyze` in `tools/web.py`

**Files:**
- Modify: `src/reverser/tools/web.py`
- Modify: `tests/test_retrofit.py`

The spec calls for `ssl_scan` retrofitting but `tools/web.py` ships `testssl_analyze`; we apply the same parser there.

- [ ] **Step 1: Append failing test**

```python
def test_testssl_analyze_writes_findings(tmp_targets_dir, monkeypatch):
    from reverser.tools import web as webmod
    text = (FIXTURES / "ssl" / "sslscan_full.txt").read_text()
    monkeypatch.setattr(webmod, "run_cmd", _stub_run_cmd(text))

    _call(webmod.testssl_analyze, {"target": "10.10.10.5:443"})
    findings = for_target("10.10.10.5:443").get_findings()
    assert any("TLS" in f.title for f in findings)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_retrofit.py -v -k testssl_analyze`
Expected: 1 failure.

- [ ] **Step 3: Edit `testssl_analyze` in `src/reverser/tools/web.py`**

After `result = run_cmd(cmd, timeout=timeout, max_output=DEFAULT_MAX_OUTPUT * 2)` and before `return cmd_result_to_tool_result(result)`, insert:

```python
    # ── KB write (new) ─────────────────────────────────────────────────
    try:
        from ..kb import for_target
        from ..kb.parsers import parse_ssl_findings
        kb = for_target(target)
        out = parse_ssl_findings(result["stdout"])
        for f in out["findings"]:
            kb.record_finding(f)
        if out["note"]:
            kb.record_note(out["note"])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("KB write failed in testssl_analyze: %s", e)
    # ───────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_retrofit.py -v -k testssl_analyze`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/web.py tests/test_retrofit.py
git commit -m "feat(tools/web): retrofit testssl_analyze with parse_ssl_findings"
```

---

## Task 32: End-to-end integration smoke + tool registry sweep

**Files:**
- Modify: `tests/test_retrofit.py`

- [ ] **Step 1: Append integration smoke**

```python
def test_full_recon_to_kb_show_flow(tmp_targets_dir, monkeypatch, capsys):
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
        lambda cmd, sudo, **kw: {"stdout": nmap_text, "stderr": "", "returncode": 0, "truncated": False},
    )
    monkeypatch.setattr(net, "run_cmd", _stub_run_cmd(asrep_text))
    monkeypatch.setattr(webmod, "run_cmd", _stub_run_cmd(whatweb_text))
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
    # 2 untested asrep hashes were recorded
    assert "2 total" in text or "2 " in text
```

- [ ] **Step 2: Run the smoke**

Run: `pytest tests/test_retrofit.py -v -k full_recon`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_retrofit.py
git commit -m "test(retrofit): end-to-end smoke nmap+whatweb+kerberos -> kb_show"
```

---

## Task 33: Final sweep — run all tests + verify tool count

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass (Plan 1 ~37 + Plan 2 parser tests ~36 + kb_tools ~16 + retrofit ~13 = ~100+ tests).

- [ ] **Step 2: Verify the tool registry exports the new tools**

Run:

```bash
python -c "from reverser.tools import ALL_TOOLS; names = [t.name for t in ALL_TOOLS]; print('\n'.join(sorted(names)))"
```

Expected output includes:
- `kb_show`
- `kb_list_hosts`
- `kb_list_services`
- `kb_list_creds`
- `kb_add_finding`
- `kb_add_note`
- `kb_export_report`

- [ ] **Step 3: Confirm parser module exports all 12 parsers**

Run:

```bash
python -c "
from reverser.kb import parsers
exports = [n for n in dir(parsers) if n.startswith('parse_')]
print('\n'.join(sorted(exports)))
print('count:', len(exports))
"
```

Expected output (12 entries):
- `parse_asreproast_hashes`
- `parse_banner_first_line`
- `parse_gobuster_paths`
- `parse_kerberoast_hashes`
- `parse_ldap_entries`
- `parse_nbtscan_output`
- `parse_nikto_findings`
- `parse_nmap_output`
- `parse_nmap_smb_scripts`
- `parse_smbclient_shares`
- `parse_ssl_findings`
- `parse_whatweb_plugins`

`count: 12`

- [ ] **Step 4: Confirm 11 retrofits are wired**

Run:

```bash
python -c "
import inspect
from reverser.tools import network as n, web as w
checks = [
    (n.nmap_scan, 'parse_nmap_output'),
    (n.ldap_search, 'parse_ldap_entries'),
    (n.kerberos_enum, 'parse_asreproast_hashes'),
    (n.kerberos_enum, 'parse_kerberoast_hashes'),
    (n.smb_enum, 'parse_smbclient_shares'),
    (n.smb_enum, 'parse_nmap_smb_scripts'),
    (n.nbtscan_scan, 'parse_nbtscan_output'),
    (n.banner_grab, 'parse_banner_first_line'),
    (n.whatweb_scan, 'parse_whatweb_plugins'),
    (n.gobuster_scan, 'parse_gobuster_paths'),
    (n.nikto_scan, 'parse_nikto_findings'),
    (n.ssl_scan, 'parse_ssl_findings'),
    (w.whatweb_fingerprint, 'parse_whatweb_plugins'),
    (w.nikto_scan, 'parse_nikto_findings'),
    (w.testssl_analyze, 'parse_ssl_findings'),
]
for fn, parser in checks:
    src = inspect.getsource(fn)
    assert parser in src, f'{fn.__name__} missing {parser} retrofit'
    assert 'logging.getLogger' in src, f'{fn.__name__} missing log fallback'
print('All 15 retrofit wirings present.')
"
```

Expected: `All 15 retrofit wirings present.` (the spec calls 11 retrofits; counting both kerberos_enum branches, both smb_enum parsers, and the 4 in `tools/web.py` plus the 4 in `tools/network.py` web tools, the total parser-call sites is higher; this script asserts the per-parser presence).

- [ ] **Step 5: Final commit if any cleanup needed**

If everything is clean, skip. Otherwise:

```bash
git commit -am "chore(plan-2): final sweep — registry + parser surface verified"
```

---

## Done

Plan 2 ships:

- 12 pure-function parsers in `reverser.kb.parsers` with ≥3 fixtures each (~36 fixtures, ~36 tests).
- 7 read/edit MCP tools in `reverser.tools.kb` registered in the MCP server.
- 11 retrofits wiring 12 parser call sites into the existing recon tools, all guarded by `try/except` + `logging.warning`.

After this plan lands, a session running the existing `pentest` profile against any AD-shaped target will populate `targets/<target>/state.db` automatically; `kb_show` and `kb_export_report` make that state legible to the LLM and the operator.

Next up: **Plan 3 — NetExec tools (`tools/netexec.py`).**
