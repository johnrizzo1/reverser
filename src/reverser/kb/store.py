"""KB public API: dataclasses + KB class for per-target SQLite access."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


_VALID_CRED_STATUS = {"untested", "invalid", "valid"}
_VALID_SEVERITY = {"info", "low", "medium", "high", "critical"}


@dataclass
class HostFact:
    ip: str
    hostname: Optional[str] = None
    os: Optional[str] = None
    domain: Optional[str] = None
    is_dc: bool = False
    smb_signing: Optional[str] = None


@dataclass
class ServiceFact:
    host_ip: str
    port: int
    proto: str
    service: Optional[str] = None
    version: Optional[str] = None
    banner: Optional[str] = None
    scan_source: Optional[str] = None


@dataclass
class CredentialFact:
    username: str
    password: Optional[str] = None
    nt_hash: Optional[str] = None
    lm_hash: Optional[str] = None
    kerberos_ticket: Optional[str] = None
    domain: Optional[str] = None
    source_tool: Optional[str] = None
    source_context: Optional[str] = None
    status: str = "untested"

    def __post_init__(self):
        if self.status not in _VALID_CRED_STATUS:
            raise ValueError(
                f"invalid credential status {self.status!r}; "
                f"must be one of {sorted(_VALID_CRED_STATUS)}"
            )


@dataclass
class FindingFact:
    title: str
    severity: str
    description: str
    evidence_paths: list[str] = field(default_factory=list)
    cvss: Optional[float] = None

    def __post_init__(self):
        if self.severity not in _VALID_SEVERITY:
            raise ValueError(
                f"invalid severity {self.severity!r}; "
                f"must be one of {sorted(_VALID_SEVERITY)}"
            )


@dataclass
class ArtifactFact:
    kind: str
    path: str
    sha256: Optional[str] = None
    source_tool: Optional[str] = None


@dataclass
class CredResult:
    service_kind: str
    target_host: str
    success: bool
    error_msg: Optional[str] = None
