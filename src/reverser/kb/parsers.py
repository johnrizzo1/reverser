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

from .store import HostFact, ServiceFact


@dataclass
class NmapHostResult:
    """Wrapper for a single nmap host with its discovered services."""
    host: HostFact
    services: list[ServiceFact]


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
                ip=current_hostname or current_dn,
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

    cleaned: list[HostFact] = []
    for h in hosts:
        if h.hostname:
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
