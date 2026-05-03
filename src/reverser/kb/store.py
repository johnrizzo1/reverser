"""KB public API: dataclasses + KB class for per-target SQLite access."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schema import apply_schema


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


def normalize_target(target: str) -> str:
    """Normalize a target identifier (lowercase, strip)."""
    if not target or not target.strip():
        raise ValueError("target identifier must be non-empty")
    return target.strip().lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


class KB:
    """Per-target knowledge base. Construct via reverser.kb.for_target(target)."""

    def __init__(self, target: str):
        self.target_id = normalize_target(target)
        self.root = _targets_root() / self.target_id
        self._init_filesystem()
        self._init_database()

    def _init_filesystem(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "findings").mkdir(exist_ok=True)
        (self.root / "loot").mkdir(exist_ok=True)

    def _init_database(self) -> None:
        self.db_path = self.root / "state.db"
        with self._connect() as conn:
            apply_schema(conn)
            now = _now_iso()
            conn.execute(
                "INSERT OR IGNORE INTO targets (id, first_seen, last_active) VALUES (?, ?, ?)",
                (self.target_id, now, now),
            )
            conn.execute(
                "UPDATE targets SET last_active = ? WHERE id = ?",
                (now, self.target_id),
            )
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def record_host(self, host: HostFact) -> None:
        """Insert or merge a host fact. None fields do not clobber existing values."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT hostname, os, domain, is_dc, smb_signing FROM hosts "
                "WHERE target_id = ? AND ip = ?",
                (self.target_id, host.ip),
            ).fetchone()
            now = _now_iso()
            if existing is None:
                conn.execute(
                    "INSERT INTO hosts "
                    "(target_id, ip, hostname, os, domain, is_dc, smb_signing, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.target_id, host.ip, host.hostname, host.os,
                        host.domain, int(host.is_dc), host.smb_signing, now,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE hosts SET "
                    "hostname = COALESCE(?, hostname), "
                    "os = COALESCE(?, os), "
                    "domain = COALESCE(?, domain), "
                    "is_dc = CASE WHEN ? = 1 THEN 1 ELSE is_dc END, "
                    "smb_signing = COALESCE(?, smb_signing) "
                    "WHERE target_id = ? AND ip = ?",
                    (
                        host.hostname, host.os, host.domain,
                        int(host.is_dc), host.smb_signing,
                        self.target_id, host.ip,
                    ),
                )
            conn.commit()

    def get_hosts(self) -> list[HostFact]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT ip, hostname, os, domain, is_dc, smb_signing FROM hosts "
                "WHERE target_id = ? ORDER BY ip",
                (self.target_id,),
            )
            return [
                HostFact(
                    ip=r[0], hostname=r[1], os=r[2], domain=r[3],
                    is_dc=bool(r[4]), smb_signing=r[5],
                )
                for r in cursor.fetchall()
            ]
