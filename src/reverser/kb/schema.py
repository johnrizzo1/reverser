"""SQLite schema DDL and lightweight migrations for the per-target KB."""

import sqlite3

SCHEMA_VERSION = 3

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS targets (
        id           TEXT PRIMARY KEY,
        hostname     TEXT,
        ip           TEXT,
        domain       TEXT,
        scope_notes  TEXT,
        first_seen   TEXT NOT NULL,
        last_active  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hosts (
        target_id    TEXT NOT NULL REFERENCES targets(id),
        ip           TEXT NOT NULL,
        hostname     TEXT,
        os           TEXT,
        domain       TEXT,
        is_dc        INTEGER NOT NULL DEFAULT 0,
        smb_signing  TEXT,
        first_seen   TEXT NOT NULL,
        PRIMARY KEY (target_id, ip)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS services (
        target_id   TEXT NOT NULL REFERENCES targets(id),
        host_ip     TEXT NOT NULL,
        port        INTEGER NOT NULL,
        proto       TEXT NOT NULL,
        service     TEXT,
        version     TEXT,
        banner      TEXT,
        scan_source TEXT,
        scanned_at  TEXT NOT NULL,
        PRIMARY KEY (target_id, host_ip, port, proto)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS credentials (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id       TEXT NOT NULL REFERENCES targets(id),
        username        TEXT NOT NULL,
        password        TEXT,
        nt_hash         TEXT,
        lm_hash         TEXT,
        kerberos_ticket TEXT,
        domain          TEXT,
        source_tool     TEXT,
        source_context  TEXT,
        status          TEXT NOT NULL,
        first_seen      TEXT NOT NULL,
        last_tested     TEXT,
        UNIQUE (target_id, username, password, nt_hash)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_unique
        ON credentials (
            target_id,
            username,
            COALESCE(password, ''),
            COALESCE(nt_hash, '')
        )
    """,
    """
    CREATE TABLE IF NOT EXISTS cred_results (
        cred_id      INTEGER NOT NULL REFERENCES credentials(id),
        service_kind TEXT NOT NULL,
        target_host  TEXT NOT NULL,
        success      INTEGER NOT NULL,
        error_msg    TEXT,
        attempted_at TEXT NOT NULL,
        PRIMARY KEY (cred_id, service_kind, target_host, attempted_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id       TEXT NOT NULL REFERENCES targets(id),
        title           TEXT NOT NULL,
        severity        TEXT NOT NULL,
        cvss            REAL,
        description     TEXT,
        evidence_paths  TEXT,
        reproduction    TEXT,
        reachability    TEXT,
        confidence      INTEGER,
        evidence_blocker TEXT,
        validated       INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id   TEXT NOT NULL REFERENCES targets(id),
        kind        TEXT NOT NULL,
        path        TEXT NOT NULL,
        sha256      TEXT,
        source_tool TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id  TEXT NOT NULL REFERENCES targets(id),
        body       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hypotheses (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id       INTEGER REFERENCES hypotheses(id) ON DELETE SET NULL,
        target_id       TEXT    NOT NULL REFERENCES targets(id),
        statement       TEXT    NOT NULL,
        rationale       TEXT,
        status          TEXT    NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','testing','confirmed','refuted','abandoned','blocked')),
        confidence      INTEGER CHECK (confidence BETWEEN 0 AND 100),
        dispatched_to   TEXT,
        dispatch_count  INTEGER NOT NULL DEFAULT 0,
        evidence_refs   TEXT,
        tags            TEXT,
        created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON hypotheses(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_hypotheses_parent ON hypotheses(parent_id)
    """,
]


_FINDING_ADDED_COLUMNS = [
    ("reproduction", "TEXT"),
    ("reachability", "TEXT"),
    ("confidence", "INTEGER"),
    ("evidence_blocker", "TEXT"),
    ("validated", "INTEGER NOT NULL DEFAULT 1"),
]


def _migrate_findings_columns(conn: sqlite3.Connection) -> None:
    """Add new finding columns to a pre-v3 table. Idempotent."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(findings)")}
    if not existing:
        return  # table will be created by _DDL
    for name, decl in _FINDING_ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE findings ADD COLUMN {name} {decl}")


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if missing, run additive migrations, stamp the version."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    for stmt in _DDL:
        conn.execute(stmt)
    _migrate_findings_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the schema version recorded in the meta table, or 0 if absent."""
    cursor = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    row = cursor.fetchone()
    return int(row[0]) if row else 0
