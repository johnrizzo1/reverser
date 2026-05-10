"""Tests for the Session.exchanges (formerly findings) refactor."""

import pytest


def test_exchange_dataclass_shape():
    from reverser.tui.session import Exchange

    e = Exchange(
        user="hi",
        agent="hello",
        turn=1,
        timestamp="2026-05-09T14:23:00",
        cost=0.01,
    )
    assert e.user == "hi"
    assert e.agent == "hello"
    assert e.turn == 1
    assert e.cost == 0.01


def test_session_exchanges_is_a_list_of_exchange(tmp_path, monkeypatch):
    """A new AgentSession has an empty exchanges list."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    assert isinstance(sess.exchanges, list)
    assert sess.exchanges == []


def test_recent_findings_strings_projects_from_exchanges(tmp_path, monkeypatch):
    """The prompt builder gets a list-of-strings projection of exchanges,
    in the legacy "User: X\\n\\nAgent: Y" combined format that preserves
    existing prompt behavior.
    """
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession, Exchange

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    sess.exchanges = [
        Exchange(user="q1", agent="a1", turn=1,
                 timestamp="2026-05-09T14:23:00", cost=0.01),
        Exchange(user="q2", agent="a2", turn=2,
                 timestamp="2026-05-09T14:24:00", cost=0.02),
    ]
    findings = sess._recent_findings_strings()
    assert findings == [
        "User: q1\n\nAgent: a1",
        "User: q2\n\nAgent: a2",
    ]
