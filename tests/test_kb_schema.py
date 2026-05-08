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
