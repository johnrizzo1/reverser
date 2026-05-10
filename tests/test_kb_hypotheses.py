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


import json

from reverser.kb.store import KB, HypothesisFact


def _fresh_kb(tmp_path, monkeypatch, target="testtarget"):
    """Create an isolated KB rooted at tmp_path."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    return KB(target)  # constructor takes the raw target string and normalizes internally


def test_add_hypothesis_returns_id_and_persists(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h = kb.add_hypothesis(
        statement="DC has SMB signing disabled",
        rationale="seen in nmap output",
        confidence=80,
        tags=["smb", "high-impact"],
    )
    assert h.id > 0
    assert h.statement == "DC has SMB signing disabled"
    assert h.status == "proposed"
    assert h.confidence == 80
    assert h.tags == ["smb", "high-impact"]


def test_update_hypothesis_changes_status_and_evidence(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h = kb.add_hypothesis(statement="x")
    kb.update_hypothesis(
        h.id,
        status="testing",
        dispatched_to="ad",
    )
    fetched = kb.get_hypothesis(h.id)
    assert fetched.status == "testing"
    assert fetched.dispatched_to == "ad"

    kb.update_hypothesis(
        h.id,
        status="confirmed",
        confidence=95,
        evidence_refs=[{"kind": "finding", "id": 12}],
    )
    fetched = kb.get_hypothesis(h.id)
    assert fetched.status == "confirmed"
    assert fetched.confidence == 95
    assert fetched.evidence_refs == [{"kind": "finding", "id": 12}]


def test_list_hypotheses_filters_by_status(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h1 = kb.add_hypothesis(statement="a")
    h2 = kb.add_hypothesis(statement="b")
    kb.update_hypothesis(h1.id, status="confirmed")

    confirmed = kb.list_hypotheses(status="confirmed")
    assert len(confirmed) == 1
    assert confirmed[0].id == h1.id

    proposed = kb.list_hypotheses(status="proposed")
    assert len(proposed) == 1
    assert proposed[0].id == h2.id


def test_list_hypotheses_filters_by_parent(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    parent = kb.add_hypothesis(statement="parent")
    child1 = kb.add_hypothesis(statement="child1", parent_id=parent.id)
    child2 = kb.add_hypothesis(statement="child2", parent_id=parent.id)
    kb.add_hypothesis(statement="orphan")  # different root

    children = kb.list_hypotheses(parent_id=parent.id)
    assert {c.id for c in children} == {child1.id, child2.id}


def test_get_hypothesis_returns_none_for_missing(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    assert kb.get_hypothesis(99999) is None


def test_dispatch_count_increments(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    h = kb.add_hypothesis(statement="x")
    assert kb.get_hypothesis(h.id).dispatch_count == 0
    kb.update_hypothesis(h.id, dispatched_to="ad", increment_dispatch_count=True)
    assert kb.get_hypothesis(h.id).dispatch_count == 1
    kb.update_hypothesis(h.id, dispatched_to="ad", increment_dispatch_count=True)
    assert kb.get_hypothesis(h.id).dispatch_count == 2
