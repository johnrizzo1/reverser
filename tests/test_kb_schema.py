"""Tests for KB schema DDL and migration logic."""

import sqlite3
import pytest

from reverser.kb.schema import SCHEMA_VERSION, apply_schema, get_schema_version


def test_apply_schema_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    assert "targets" in tables
    assert "hosts" in tables
    assert "services" in tables
    assert "credentials" in tables
    assert "cred_results" in tables
    assert "findings" in tables
    assert "artifacts" in tables
    assert "notes" in tables
    assert "meta" in tables
    conn.close()


def test_schema_version_recorded(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    assert get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_apply_schema_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    apply_schema(conn)  # second call must not raise
    assert get_schema_version(conn) == SCHEMA_VERSION
    conn.close()


def test_credentials_unique_constraint(tmp_path):
    """Same (target, username, password, nt_hash) tuple cannot be inserted twice."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO targets (id, first_seen, last_active) VALUES (?, ?, ?)",
        ("10.10.10.5", "2026-05-03T00:00:00", "2026-05-03T00:00:00"),
    )
    conn.execute(
        "INSERT INTO credentials (target_id, username, password, status, first_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        ("10.10.10.5", "jdoe", "secret", "untested", "2026-05-03T00:00:00"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO credentials (target_id, username, password, status, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            ("10.10.10.5", "jdoe", "secret", "untested", "2026-05-03T00:00:00"),
        )
    conn.close()


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_findings_has_new_columns_on_fresh_db():
    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    cols = _columns(conn, "findings")
    for c in ("reproduction", "reachability", "confidence", "evidence_blocker", "validated"):
        assert c in cols
    assert get_schema_version(conn) == SCHEMA_VERSION


def test_migration_adds_columns_to_legacy_findings_table():
    conn = sqlite3.connect(":memory:")
    # simulate a v2 findings table without the new columns
    conn.execute(
        "CREATE TABLE findings (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT, "
        "title TEXT NOT NULL, severity TEXT NOT NULL, cvss REAL, description TEXT, "
        "evidence_paths TEXT, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO findings (target_id, title, severity, created_at) "
        "VALUES ('t', 'old', 'low', '2026-01-01T00:00:00')"
    )
    conn.commit()
    apply_schema(conn)
    cols = _columns(conn, "findings")
    for c in ("reproduction", "reachability", "confidence", "evidence_blocker", "validated"):
        assert c in cols
    # legacy row still present and readable
    row = conn.execute("SELECT title, reachability, validated FROM findings").fetchone()
    assert row[0] == "old"
    assert row[1] is None        # legacy default
    assert row[2] == 1           # new column default
