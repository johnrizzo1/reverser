"""Tests for canned-query catalog + free-form query write detection."""

import pytest

from reverser.tools.bloodhound import (
    CANNED_QUERIES,
    _detect_writes,
)


EXPECTED_QUERY_NAMES = [
    "kerberoastable_users",
    "asreproastable_users",
    "shortest_path_to_da",
    "computers_where_user_admin",
    "users_with_dcsync",
    "unconstrained_delegation",
    "constrained_delegation",
    "password_not_required",
    "computers_no_laps",
    "foreign_group_membership",
    "owned_to_high_value",
    "sessions_on_target",
    "high_value_targets",
    "domain_admins",
    "kerberos_delegation_summary",
]


def test_canned_query_catalog_has_all_15():
    assert set(CANNED_QUERIES.keys()) == set(EXPECTED_QUERY_NAMES)


@pytest.mark.parametrize("name", EXPECTED_QUERY_NAMES)
def test_each_canned_query_is_nonempty_string(name):
    assert isinstance(CANNED_QUERIES[name], str)
    assert "MATCH" in CANNED_QUERIES[name].upper()


@pytest.mark.parametrize("name", EXPECTED_QUERY_NAMES)
def test_no_canned_query_has_writes(name):
    assert _detect_writes(CANNED_QUERIES[name]) is False


@pytest.mark.parametrize("cypher", [
    "MATCH (n) RETURN n",
    "MATCH (u:User) WHERE u.name = 'x' RETURN u",
    "MATCH p = shortestPath((a)-[*]->(b)) RETURN p",
    "match (n) return count(n)",
])
def test_detect_writes_returns_false_for_reads(cypher):
    assert _detect_writes(cypher) is False


@pytest.mark.parametrize("cypher", [
    "CREATE (n:User {name: 'x'})",
    "MERGE (n:Group {name: 'a'})",
    "MATCH (n) DELETE n",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) SET n.x = 1",
    "MATCH (n) REMOVE n.x",
    "create (n:X)",
    "MATCH (n) CALL apoc.create.node(['L'], {}) YIELD node RETURN node",
    "DROP CONSTRAINT foo",
    "MATCH (n)-[r]->(m) SET r.x = 1",
])
def test_detect_writes_returns_true_for_writes(cypher):
    assert _detect_writes(cypher) is True


def test_detect_writes_is_naive_about_strings():
    """Naive regex catches the literal word 'CREATE' inside a string. Documented behavior."""
    cypher = 'MATCH (n) WHERE n.note = "CREATE" RETURN n'
    assert _detect_writes(cypher) is True


import asyncio
from unittest.mock import patch, MagicMock


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def test_bloodhound_query_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_query
    result = _call(bloodhound_query, {"target": "10.10.10.5", "cypher": "MATCH (n) RETURN n"})
    assert result.get("is_error") is True


def test_bloodhound_query_rejects_writes_by_default(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_query
    result = _call(bloodhound_query, {
        "target": "10.10.10.5",
        "cypher": "CREATE (n:Bogus)",
    })
    assert result.get("is_error") is True
    assert "allow_writes" in result["content"][0]["text"]


def test_bloodhound_query_runs_read(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_record = MagicMock()
    fake_record.data.return_value = {"name": "Alice"}
    fake_session.run.return_value = [fake_record]
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_query
        result = _call(bloodhound_query, {
            "target": "10.10.10.5",
            "cypher": "MATCH (u:User) RETURN u.name AS name",
        })
    assert result.get("is_error") is not True
    assert "Alice" in result["content"][0]["text"]


def test_bloodhound_query_passes_params(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_query
        _call(bloodhound_query, {
            "target": "10.10.10.5",
            "cypher": "MATCH (u:User {name: $name}) RETURN u",
            "params": {"name": "jdoe"},
        })
    fake_session.run.assert_called_once()
    call_args = fake_session.run.call_args
    assert {"name": "jdoe"} in call_args.args or call_args.kwargs.get("parameters") == {"name": "jdoe"}


def test_bloodhound_query_allow_writes_passes(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_query
        result = _call(bloodhound_query, {
            "target": "10.10.10.5",
            "cypher": "CREATE (n:_Test)",
            "allow_writes": True,
        })
    assert result.get("is_error") is not True


def test_bloodhound_canned_requires_auth(tmp_targets_dir, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    monkeypatch.chdir(tmp_targets_dir)
    from reverser.tools.bloodhound import bloodhound_canned
    result = _call(bloodhound_canned, {"target": "10.10.10.5", "query_name": "domain_admins"})
    assert result.get("is_error") is True


def test_bloodhound_canned_unknown_name(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.bloodhound import bloodhound_canned
    result = _call(bloodhound_canned, {"target": "10.10.10.5", "query_name": "no_such_query"})
    assert result.get("is_error") is True
    assert "no_such_query" in result["content"][0]["text"]
    assert "domain_admins" in result["content"][0]["text"]


@pytest.mark.parametrize("name", EXPECTED_QUERY_NAMES)
def test_bloodhound_canned_runs_each(name, tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    params = {}
    if name in ("computers_where_user_admin", "owned_to_high_value"):
        params = {"username": "jdoe@CORP.LOCAL"}
    if name == "sessions_on_target":
        params = {"computer": "WS01@CORP.LOCAL"}
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_canned
        result = _call(bloodhound_canned, {
            "target": "10.10.10.5",
            "query_name": name,
            "params": params,
        })
    assert result.get("is_error") is not True, f"{name}: {result['content'][0]['text']}"
    fake_session.run.assert_called_once()


def test_bloodhound_canned_passes_params(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    fake_session = MagicMock()
    fake_session.run.return_value = []
    fake_session.__enter__ = lambda s: s
    fake_session.__exit__ = lambda *a: None
    fake_driver = MagicMock()
    fake_driver.session.return_value = fake_session
    with patch("reverser.tools.bloodhound._get_neo4j_driver", return_value=fake_driver):
        from reverser.tools.bloodhound import bloodhound_canned
        _call(bloodhound_canned, {
            "target": "10.10.10.5",
            "query_name": "owned_to_high_value",
            "params": {"username": "jdoe@CORP.LOCAL"},
        })
    call = fake_session.run.call_args
    assert {"username": "jdoe@CORP.LOCAL"} in call.args or call.kwargs.get("parameters") == {"username": "jdoe@CORP.LOCAL"}
