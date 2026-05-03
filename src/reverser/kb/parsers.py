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

from .store import HostFact, ServiceFact, CredentialFact, FindingFact


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
            if current_dn and "OU=Domain Controllers" in current_dn:
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

    share_lines = re.findall(r"\\\\\S+\\(\S+):", text)
    note_parts = []
    if share_lines:
        note_parts.append("nmap smb-enum-shares: " + ", ".join(sorted(set(share_lines))))
    note = "\n".join(note_parts) if note_parts else "nmap SMB scripts: no shares enumerated"

    return {"host": host, "services": services, "note": note}


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
        title = body if len(body) <= 200 else body[:197] + "..."
        findings.append(FindingFact(
            title=title,
            severity=_nikto_severity_for(body),
            description=body,
        ))
    return findings


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
