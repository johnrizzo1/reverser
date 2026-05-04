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
