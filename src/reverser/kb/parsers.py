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
