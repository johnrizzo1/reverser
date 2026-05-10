"""Tests for the hypotheses table and store helpers."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from reverser.kb.schema import apply_schema, get_schema_version, SCHEMA_VERSION


def test_schema_version_is_2():
    """Bumping the schema for the hypotheses table."""
    assert SCHEMA_VERSION == 2


def test_apply_schema_creates_hypotheses_table():
    """Fresh DB has a hypotheses table after apply_schema."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        apply_schema(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hypotheses'"
        )
        assert cur.fetchone() is not None


def test_apply_schema_creates_hypotheses_indexes():
    """Indexes on status and parent_id exist."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        apply_schema(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='hypotheses'"
        )
        index_names = {r[0] for r in cur.fetchall()}
        assert "idx_hypotheses_status" in index_names
        assert "idx_hypotheses_parent" in index_names


def test_status_check_constraint_rejects_invalid_status():
    """The CHECK constraint blocks unknown status values."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        apply_schema(conn)
        # Insert a target row first (FK from hypotheses to targets)
        conn.execute(
            "INSERT INTO targets (id, first_seen, last_active) VALUES (?, ?, ?)",
            ("test", "2026-05-09", "2026-05-09"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO hypotheses (target_id, statement, status) "
                "VALUES (?, ?, ?)",
                ("test", "test statement", "bogus"),
            )


def test_apply_schema_migrates_existing_v1_db():
    """Running apply_schema on a v1 DB (without hypotheses table) adds the table."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        # Simulate a v1 DB: minimal tables only
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '1')"
        )
        conn.execute(
            "CREATE TABLE targets (id TEXT PRIMARY KEY, first_seen TEXT, last_active TEXT)"
        )
        conn.commit()
        # Now apply the new schema
        apply_schema(conn)
        # Hypotheses table should now exist
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hypotheses'"
        )
        assert cur.fetchone() is not None
        # Schema version is now 2
        assert get_schema_version(conn) == 2
