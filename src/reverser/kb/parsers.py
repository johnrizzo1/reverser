"""Pure-function parsers that turn captured tool stdout into KB facts.

Every parser in this module is a pure function: no I/O, no logging, no
global state. Each one accepts a `text: str` (a tool's captured stdout)
and returns dataclasses defined in `reverser.kb.store`.

Parsers are intentionally tolerant: empty input returns an empty result,
malformed input returns whatever can be salvaged. They never raise on
unrecognised lines — the worst case is an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import HostFact, ServiceFact


@dataclass
class NmapHostResult:
    """Wrapper for a single nmap host with its discovered services."""
    host: HostFact
    services: list[ServiceFact]
