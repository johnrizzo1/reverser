"""KB public API: dataclasses + KB class for per-target SQLite access."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .schema import apply_schema
from reverser.paths import targets_root


def _emit_kb_change(target: str, *tables: str) -> None:
    try:
        from reverser.gui_service.kb_emitter import emit_kb_change
        emit_kb_change(target, *tables)
    except Exception:
        pass


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
    id: Optional[int] = None

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


@dataclass
class HypothesisFact:
    id: int | None = None
    parent_id: int | None = None
    statement: str = ""
    rationale: str | None = None
    status: str = "proposed"
    confidence: int | None = None
    dispatched_to: str | None = None
    dispatch_count: int = 0
    evidence_refs: list[dict] | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None


def normalize_target(target: str) -> str:
    """Normalize a target identifier (lowercase, strip)."""
    if not target or not target.strip():
        raise ValueError("target identifier must be non-empty")
    return target.strip().lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KB:
    """Per-target knowledge base. Construct via reverser.kb.for_target(target)."""

    def __init__(self, target: str):
        self.target_id = normalize_target(target)
        self.root = targets_root() / self.target_id
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
        _emit_kb_change(self.target_id, "hosts")

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

    def record_service(self, svc: ServiceFact) -> None:
        with self._connect() as conn:
            now = _now_iso()
            conn.execute(
                "INSERT INTO services "
                "(target_id, host_ip, port, proto, service, version, banner, scan_source, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (target_id, host_ip, port, proto) DO UPDATE SET "
                "  service = COALESCE(excluded.service, service), "
                "  version = COALESCE(excluded.version, version), "
                "  banner = COALESCE(excluded.banner, banner), "
                "  scan_source = COALESCE(excluded.scan_source, scan_source), "
                "  scanned_at = excluded.scanned_at",
                (
                    self.target_id, svc.host_ip, svc.port, svc.proto,
                    svc.service, svc.version, svc.banner, svc.scan_source, now,
                ),
            )
            conn.commit()
        _emit_kb_change(self.target_id, "services")

    def get_services(
        self, host_ip: str | None = None, port: int | None = None,
    ) -> list[ServiceFact]:
        sql = (
            "SELECT host_ip, port, proto, service, version, banner, scan_source "
            "FROM services WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if host_ip is not None:
            sql += " AND host_ip = ?"
            params.append(host_ip)
        if port is not None:
            sql += " AND port = ?"
            params.append(port)
        sql += " ORDER BY host_ip, port"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return [
                ServiceFact(
                    host_ip=r[0], port=r[1], proto=r[2],
                    service=r[3], version=r[4], banner=r[5], scan_source=r[6],
                )
                for r in cursor.fetchall()
            ]

    _STATUS_RANK = {"untested": 0, "invalid": 1, "valid": 2}

    def record_credential(self, cred: CredentialFact) -> int:
        """Record a credential. Returns the row id.

        Behavior:
        - Dedupes on (target, username, password, nt_hash). NULLs collapse via COALESCE.
        - Status only moves up the ladder untested → invalid → valid (never down).
        """
        with self._connect() as conn:
            now = _now_iso()
            existing = conn.execute(
                "SELECT id, status FROM credentials WHERE "
                "target_id = ? AND username = ? AND "
                "COALESCE(password, '') = COALESCE(?, '') AND "
                "COALESCE(nt_hash, '') = COALESCE(?, '')",
                (self.target_id, cred.username, cred.password, cred.nt_hash),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    "INSERT INTO credentials "
                    "(target_id, username, password, nt_hash, lm_hash, kerberos_ticket, "
                    " domain, source_tool, source_context, status, first_seen, last_tested) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.target_id, cred.username, cred.password, cred.nt_hash,
                        cred.lm_hash, cred.kerberos_ticket, cred.domain,
                        cred.source_tool, cred.source_context, cred.status,
                        now, now if cred.status != "untested" else None,
                    ),
                )
                conn.commit()
                _emit_kb_change(self.target_id, "credentials")
                assert cursor.lastrowid is not None  # AUTOINCREMENT guarantees this
                return cursor.lastrowid
            cred_id, current_status = existing
            new_status = (
                cred.status
                if self._STATUS_RANK[cred.status] > self._STATUS_RANK[current_status]
                else current_status
            )
            conn.execute(
                "UPDATE credentials SET status = ?, last_tested = ? WHERE id = ?",
                (new_status, now, cred_id),
            )
            conn.commit()
            _emit_kb_change(self.target_id, "credentials")
            return cred_id

    def get_credentials(self, status: str | None = None) -> list[CredentialFact]:
        sql = (
            "SELECT username, password, nt_hash, lm_hash, kerberos_ticket, domain, "
            "       source_tool, source_context, status "
            "FROM credentials WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return [
                CredentialFact(
                    username=r[0], password=r[1], nt_hash=r[2], lm_hash=r[3],
                    kerberos_ticket=r[4], domain=r[5],
                    source_tool=r[6], source_context=r[7], status=r[8],
                )
                for r in cursor.fetchall()
            ]

    def record_cred_result(self, cred_id: int, result: CredResult) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cred_results "
                "(cred_id, service_kind, target_host, success, error_msg, attempted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cred_id, result.service_kind, result.target_host,
                    int(result.success), result.error_msg, _now_iso(),
                ),
            )
            conn.commit()
        _emit_kb_change(self.target_id, "credentials")

    def get_cred_results(self, cred_id: int) -> list[CredResult]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT service_kind, target_host, success, error_msg "
                "FROM cred_results WHERE cred_id = ? ORDER BY attempted_at",
                (cred_id,),
            )
            return [
                CredResult(
                    service_kind=r[0], target_host=r[1],
                    success=bool(r[2]), error_msg=r[3],
                )
                for r in cursor.fetchall()
            ]

    def record_finding(self, finding: FindingFact) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO findings "
                "(target_id, title, severity, cvss, description, evidence_paths, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.target_id, finding.title, finding.severity, finding.cvss,
                    finding.description, json.dumps(finding.evidence_paths), _now_iso(),
                ),
            )
            conn.commit()
            assert cursor.lastrowid is not None  # AUTOINCREMENT guarantees this
            _emit_kb_change(self.target_id, "findings")
            return cursor.lastrowid

    def append_finding_evidence(self, finding_id: int, path: str) -> None:
        """Append a path to an existing finding's evidence_paths JSON list.

        Raises ValueError if no finding with that id exists for this target.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT evidence_paths FROM findings WHERE id = ? AND target_id = ?",
                (finding_id, self.target_id),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"No finding with id={finding_id} for target={self.target_id}"
                )
            paths = json.loads(row[0]) if row[0] else []
            paths.append(path)
            conn.execute(
                "UPDATE findings SET evidence_paths = ? WHERE id = ? AND target_id = ?",
                (json.dumps(paths), finding_id, self.target_id),
            )
            conn.commit()
        _emit_kb_change(self.target_id, "findings", "artifacts")

    def get_findings(self, severity: str | None = None) -> list[FindingFact]:
        sql = (
            "SELECT id, title, severity, cvss, description, evidence_paths "
            "FROM findings WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        sql += " ORDER BY id"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return [
                FindingFact(
                    id=r[0], title=r[1], severity=r[2], cvss=r[3],
                    description=r[4] or "",
                    evidence_paths=json.loads(r[5]) if r[5] else [],
                )
                for r in cursor.fetchall()
            ]

    def record_artifact(self, artifact: ArtifactFact) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO artifacts (target_id, kind, path, sha256, source_tool, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.target_id, artifact.kind, artifact.path,
                    artifact.sha256, artifact.source_tool, _now_iso(),
                ),
            )
            conn.commit()
            assert cursor.lastrowid is not None  # AUTOINCREMENT guarantees this
            _emit_kb_change(self.target_id, "artifacts")
            return cursor.lastrowid

    def get_artifacts(self) -> list[ArtifactFact]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT kind, path, sha256, source_tool FROM artifacts "
                "WHERE target_id = ? ORDER BY id",
                (self.target_id,),
            )
            return [
                ArtifactFact(kind=r[0], path=r[1], sha256=r[2], source_tool=r[3])
                for r in cursor.fetchall()
            ]

    def record_note(self, body: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO notes (target_id, body, created_at) VALUES (?, ?, ?)",
                (self.target_id, body, _now_iso()),
            )
            conn.commit()
        _emit_kb_change(self.target_id, "notes")

    def get_notes(self) -> list[str]:
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT body FROM notes WHERE target_id = ? ORDER BY id",
                (self.target_id,),
            )
            return [r[0] for r in cursor.fetchall()]

    # ── Hypothesis CRUD ────────────────────────────────────────────────

    def add_hypothesis(
        self,
        statement: str,
        *,
        parent_id: int | None = None,
        rationale: str | None = None,
        confidence: int | None = None,
        tags: list[str] | None = None,
    ) -> HypothesisFact:
        """Insert a new hypothesis. Returns the persisted fact with id populated."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO hypotheses "
                "(target_id, parent_id, statement, rationale, confidence, tags, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
                (
                    self.target_id, parent_id, statement, rationale, confidence,
                    json.dumps(tags) if tags is not None else None,
                    _now_iso(), _now_iso(),
                ),
            )
            new_id = cur.lastrowid
            conn.commit()
        return self.get_hypothesis(new_id)

    def update_hypothesis(
        self,
        hypothesis_id: int,
        *,
        status: str | None = None,
        rationale: str | None = None,
        confidence: int | None = None,
        dispatched_to: str | None = None,
        evidence_refs: list[dict] | None = None,
        tags: list[str] | None = None,
        increment_dispatch_count: bool = False,
    ) -> None:
        """Update fields on an existing hypothesis. Only provided kwargs are written."""
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if rationale is not None:
            sets.append("rationale = ?")
            params.append(rationale)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(confidence)
        if dispatched_to is not None:
            sets.append("dispatched_to = ?")
            params.append(dispatched_to)
        if evidence_refs is not None:
            sets.append("evidence_refs = ?")
            params.append(json.dumps(evidence_refs))
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags))
        if increment_dispatch_count:
            sets.append("dispatch_count = dispatch_count + 1")
        if not sets:
            return  # nothing to do
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(hypothesis_id)
        params.append(self.target_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE hypotheses SET {', '.join(sets)} "
                "WHERE id = ? AND target_id = ?",
                tuple(params),
            )
            conn.commit()

    def get_hypothesis(self, hypothesis_id: int) -> HypothesisFact | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id, parent_id, statement, rationale, status, confidence, "
                "dispatched_to, dispatch_count, evidence_refs, tags, "
                "created_at, updated_at "
                "FROM hypotheses WHERE id = ? AND target_id = ?",
                (hypothesis_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._row_to_hypothesis(row)

    def list_hypotheses(
        self,
        *,
        status: str | None = None,
        parent_id: int | None = None,
    ) -> list[HypothesisFact]:
        sql = (
            "SELECT id, parent_id, statement, rationale, status, confidence, "
            "dispatched_to, dispatch_count, evidence_refs, tags, "
            "created_at, updated_at "
            "FROM hypotheses WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if parent_id is not None:
            sql += " AND parent_id = ?"
            params.append(parent_id)
        sql += " ORDER BY id"
        with self._connect() as conn:
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [self._row_to_hypothesis(r) for r in rows]

    @staticmethod
    def _row_to_hypothesis(row) -> HypothesisFact:
        return HypothesisFact(
            id=row[0],
            parent_id=row[1],
            statement=row[2],
            rationale=row[3],
            status=row[4],
            confidence=row[5],
            dispatched_to=row[6],
            dispatch_count=row[7],
            evidence_refs=json.loads(row[8]) if row[8] else None,
            tags=json.loads(row[9]) if row[9] else None,
            created_at=row[10],
            updated_at=row[11],
        )

    def hypothesis_tree(self, root_id: int | None = None):
        """Return hierarchical view of hypotheses.

        If root_id is None, returns a list of {"hypothesis": HypothesisFact,
        "children": [...]} branches for all root hypotheses (parent_id IS NULL).

        If root_id is given, returns a single branch dict rooted at that hypothesis.
        Raises KeyError if root_id doesn't exist.
        """
        # Fetch all hypotheses for this target, build parent_id → children map
        all_hypotheses = self.list_hypotheses()
        by_parent: dict[int | None, list[HypothesisFact]] = {}
        for h in all_hypotheses:
            by_parent.setdefault(h.parent_id, []).append(h)

        def build_branch(h: HypothesisFact) -> dict:
            return {
                "hypothesis": h,
                "children": [build_branch(c) for c in by_parent.get(h.id, [])],
            }

        if root_id is None:
            roots = by_parent.get(None, [])
            return [build_branch(r) for r in roots]
        else:
            target = next((h for h in all_hypotheses if h.id == root_id), None)
            if target is None:
                raise KeyError(f"hypothesis {root_id} not found")
            return build_branch(target)

    def resolve_evidence_refs(self, refs: list[dict]) -> list[dict]:
        """Dereference evidence_refs into [{kind, id, data}] tuples.

        Unknown kinds are silently dropped (defensive against schema drift).
        Missing rows are silently dropped (defensive against deletion).
        """
        out: list[dict] = []
        for ref in refs:
            kind = ref.get("kind")
            ref_id = ref.get("id")
            if kind is None or ref_id is None:
                continue
            data = None
            if kind == "finding":
                data = self._get_finding_by_id(ref_id)
            elif kind == "note":
                data = self._get_note_by_id(ref_id)
            elif kind == "credential":
                data = self._get_credential_by_id(ref_id)
            elif kind == "service":
                data = self._get_service_by_id(ref_id)
            else:
                continue  # unknown kind
            if data is None:
                continue
            out.append({"kind": kind, "id": ref_id, "data": data})
        return out

    def _get_finding_by_id(self, finding_id: int):
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT title, severity, cvss, description, evidence_paths, created_at "
                "FROM findings WHERE id = ? AND target_id = ?",
                (finding_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return FindingFact(
            title=row[0], severity=row[1], cvss=row[2],
            description=row[3],
            evidence_paths=json.loads(row[4]) if row[4] else [],
        )

    def _get_note_by_id(self, note_id: int):
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT body, created_at FROM notes WHERE id = ? AND target_id = ?",
                (note_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"body": row[0], "created_at": row[1]}

    def _get_credential_by_id(self, cred_id: int):
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT username, password, nt_hash, domain, status "
                "FROM credentials WHERE id = ? AND target_id = ?",
                (cred_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return CredentialFact(
            username=row[0], password=row[1], nt_hash=row[2],
            domain=row[3], status=row[4],
        )

    def _get_service_by_id(self, service_row_id: int):
        # services has a composite PK (target_id, host_ip, port, proto) — id refs
        # are by rowid here. Defensive: if missing, return None.
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT host_ip, port, proto, service, version "
                "FROM services WHERE rowid = ? AND target_id = ?",
                (service_row_id, self.target_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        return ServiceFact(
            host_ip=row[0], port=row[1], proto=row[2],
            service=row[3], version=row[4],
        )
