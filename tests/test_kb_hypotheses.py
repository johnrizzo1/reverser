"""Tests for the hypotheses table and store helpers."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from reverser.kb.schema import apply_schema, get_schema_version, SCHEMA_VERSION


def test_schema_version_is_3():
    """v3 adds the validated-output finding columns (reproduction, reachability,
    confidence, evidence_blocker, validated)."""
    assert SCHEMA_VERSION == 3


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
        # Schema version is now 3
        assert get_schema_version(conn) == SCHEMA_VERSION


import json

from reverser.kb.store import KB, HypothesisFact


def _fresh_kb(tmp_path, monkeypatch, target="testtarget"):
    """Create an isolated KB rooted at tmp_path.

    Clears the per-process KB cache so subsequent for_target() calls (used
    inside the @tool-wrapped MCP functions) see this test's fresh dir.
    """
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
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


def test_hypothesis_tree_returns_nested_structure(tmp_path, monkeypatch):
    """tree returns roots with .children populated recursively."""
    kb = _fresh_kb(tmp_path, monkeypatch)
    root = kb.add_hypothesis(statement="root")
    child = kb.add_hypothesis(statement="child", parent_id=root.id)
    grand = kb.add_hypothesis(statement="grandchild", parent_id=child.id)
    other_root = kb.add_hypothesis(statement="other root")

    tree = kb.hypothesis_tree()
    # tree is a list of dicts: [{"hypothesis": HypothesisFact, "children": [...]}]
    assert len(tree) == 2
    # find the "root" branch
    root_branch = next(b for b in tree if b["hypothesis"].id == root.id)
    assert len(root_branch["children"]) == 1
    child_branch = root_branch["children"][0]
    assert child_branch["hypothesis"].id == child.id
    assert len(child_branch["children"]) == 1
    assert child_branch["children"][0]["hypothesis"].id == grand.id
    # other_root has no children
    other_branch = next(b for b in tree if b["hypothesis"].id == other_root.id)
    assert other_branch["children"] == []


def test_hypothesis_tree_with_root_id_returns_subtree(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    root = kb.add_hypothesis(statement="root")
    child = kb.add_hypothesis(statement="child", parent_id=root.id)
    kb.add_hypothesis(statement="orphan")

    subtree = kb.hypothesis_tree(root_id=root.id)
    # subtree returns a single branch dict (not a list)
    assert subtree["hypothesis"].id == root.id
    assert len(subtree["children"]) == 1
    assert subtree["children"][0]["hypothesis"].id == child.id


def test_resolve_evidence_refs_returns_finding_rows(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    from reverser.kb.store import FindingFact
    finding_id = kb.record_finding(FindingFact(
        title="SMB signing disabled",
        severity="medium",
        description="…",
    ))
    refs = [{"kind": "finding", "id": finding_id}]
    resolved = kb.resolve_evidence_refs(refs)
    assert len(resolved) == 1
    assert resolved[0]["kind"] == "finding"
    assert resolved[0]["data"].title == "SMB signing disabled"


def test_resolve_evidence_refs_skips_unknown_kinds(tmp_path, monkeypatch):
    """Unknown kinds are dropped rather than raising — defensive against schema drift."""
    kb = _fresh_kb(tmp_path, monkeypatch)
    refs = [{"kind": "alien_artifact", "id": 99}]
    resolved = kb.resolve_evidence_refs(refs)
    assert resolved == []


import asyncio


def _call_tool(tool_obj, args):
    """Invoke an SDK tool object's underlying coroutine.

    The claude_agent_sdk @tool decorator returns an SdkMcpTool whose callable
    lives on .handler. Fall back to .fn or calling the object directly for
    forward/backward compatibility.
    """
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def test_kb_add_hypothesis_tool_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_add_hypothesis

    result = _call_tool(kb_add_hypothesis, {
        "target": "10.10.10.5",
        "statement": "DC has SMB signing disabled",
        "rationale": "from nmap output",
        "confidence": 80,
        "tags": ["smb", "high-impact"],
    })
    assert "id" in result["content"][0]["text"] or "id" in str(result)
    # verify persistence
    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    hypotheses = kb.list_hypotheses()
    assert len(hypotheses) == 1
    assert hypotheses[0].statement == "DC has SMB signing disabled"
    assert hypotheses[0].confidence == 80


def test_kb_get_hypothesis_tool_returns_record_with_children(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_get_hypothesis

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    parent = kb.add_hypothesis(statement="parent")
    child = kb.add_hypothesis(statement="child", parent_id=parent.id)

    result = _call_tool(kb_get_hypothesis, {
        "target": "10.10.10.5",
        "id": parent.id,
    })
    text = result["content"][0]["text"]
    assert "parent" in text
    assert str(child.id) in text  # children listed


def test_kb_update_hypothesis_tool_changes_status(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_update_hypothesis

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    h = kb.add_hypothesis(statement="x")
    result = _call_tool(kb_update_hypothesis, {
        "target": "10.10.10.5",
        "id": h.id,
        "status": "confirmed",
        "confidence": 95,
        "evidence_refs": [{"kind": "finding", "id": 1}],
    })
    text = result["content"][0]["text"]
    assert "updated" in text.lower()

    fetched = kb.get_hypothesis(h.id)
    assert fetched.status == "confirmed"
    assert fetched.confidence == 95
    assert fetched.evidence_refs == [{"kind": "finding", "id": 1}]


def test_kb_list_hypotheses_tool_returns_table(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_list_hypotheses

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    kb.add_hypothesis(statement="alpha")
    kb.add_hypothesis(statement="beta")

    result = _call_tool(kb_list_hypotheses, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "alpha" in text
    assert "beta" in text


def test_kb_list_hypotheses_tool_with_include_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_list_hypotheses

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    parent = kb.add_hypothesis(statement="parent")
    kb.add_hypothesis(statement="child", parent_id=parent.id)

    result = _call_tool(kb_list_hypotheses, {
        "target": "10.10.10.5",
        "include_tree": True,
    })
    text = result["content"][0]["text"]
    # The tree-rendered output indents children
    assert "parent" in text
    assert "child" in text
    # Child should appear indented (after the parent line)
    parent_idx = text.index("parent")
    child_idx = text.index("child")
    assert child_idx > parent_idx


def test_kb_export_report_includes_attack_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_export_report

    kb = _fresh_kb(tmp_path, monkeypatch, target="10.10.10.5")
    parent = kb.add_hypothesis(statement="DC SMB signing off", confidence=95)
    kb.update_hypothesis(parent.id, status="confirmed")
    kb.add_hypothesis(statement="NTLM relay viable", parent_id=parent.id)

    result = _call_tool(kb_export_report, {"target": "10.10.10.5"})
    text = result["content"][0]["text"]
    assert "## Attack tree" in text
    assert "DC SMB signing off" in text
    assert "NTLM relay viable" in text


def test_kb_export_report_omits_attack_tree_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.kb import kb_export_report

    _fresh_kb(tmp_path, monkeypatch, target="10.10.10.6")  # empty
    result = _call_tool(kb_export_report, {"target": "10.10.10.6"})
    text = result["content"][0]["text"]
    assert "## Attack tree" not in text
