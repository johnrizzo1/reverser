"""Tests for the AgentSession lifecycle: active → stopped / completed,
and the per-turn autosave hook."""

import os
import pytest


def test_stop_transitions_to_stopped_state(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    sid = sess._snapshot.session_id
    sess.stop()

    loaded = load(sess.target, sid)
    assert loaded.state == "stopped"
    assert loaded.stopped_at is not None
    assert loaded.pid is None
    assert sess._cancel is True
    assert sess._stop_requested is True


def test_mark_completed_transitions_to_completed_state(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    sid = sess._snapshot.session_id
    sess.mark_completed()

    loaded = load(sess.target, sid)
    assert loaded.state == "completed"
    assert loaded.stopped_at is not None
    assert loaded.pid is None


def test_stop_after_completed_is_noop_on_state(tmp_path, monkeypatch):
    """completed is terminal; stop() shouldn't downgrade it."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    sid = sess._snapshot.session_id
    sess.mark_completed()
    sess.stop()  # should not transition completed → stopped

    loaded = load(sess.target, sid)
    assert loaded.state == "completed"


def test_per_turn_autosave_updates_snapshot(tmp_path, monkeypatch):
    """_autosave_snapshot() rewrites the snapshot with current stats + exchanges."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession, Exchange
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    sid = sess._snapshot.session_id

    sess.exchanges.append(Exchange(
        user="hi", agent="hello", turn=1,
        timestamp="2026-05-09T14:23:00", cost=0.01,
    ))
    sess.stats.total_cost = 0.01
    sess.stats.turns = 1
    sess._autosave_snapshot()

    loaded = load(sess.target, sid)
    assert loaded.stats.turns == 1
    assert loaded.stats.total_cost == 0.01
    assert len(loaded.conversation) == 1
    assert loaded.conversation[0].user == "hi"

    sess.exchanges.append(Exchange(
        user="next", agent="ok", turn=2,
        timestamp="2026-05-09T14:24:00", cost=0.02,
    ))
    sess.stats.total_cost = 0.03
    sess.stats.turns = 2
    sess._autosave_snapshot()

    loaded = load(sess.target, sid)
    assert loaded.stats.turns == 2
    assert loaded.stats.total_cost == 0.03
    assert len(loaded.conversation) == 2


def test_update_budget_preserves_conversation(tmp_path, monkeypatch):
    """Regression: /budget should mutate the session in place, NOT discard
    the conversation history. Previously /budget called _init_session which
    constructed a fresh AgentSession and silently wiped exchanges/stats."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession, Exchange
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
        budget=5.0,
        max_turns=50,
    )
    sid = sess._snapshot.session_id

    # Build up some conversation history
    sess.exchanges.append(Exchange(
        user="hi", agent="hello", turn=1,
        timestamp="2026-05-12T10:00:00", cost=0.01,
    ))
    sess.exchanges.append(Exchange(
        user="continue", agent="sure", turn=2,
        timestamp="2026-05-12T10:01:00", cost=0.02,
    ))
    sess.stats.total_cost = 0.03
    sess.stats.turns = 2
    sess._autosave_snapshot()

    # Bump the budget mid-engagement
    sess.update_budget(20.0)

    # In-memory state: budget updated everywhere
    assert sess.budget == 20.0
    assert sess.stats.budget == 20.0
    assert sess._snapshot.config.budget == 20.0

    # Conversation history MUST survive
    assert len(sess.exchanges) == 2
    assert sess.exchanges[0].user == "hi"
    assert sess.exchanges[1].user == "continue"
    assert sess.stats.total_cost == 0.03
    assert sess.stats.turns == 2

    # Persisted snapshot: budget bumped, conversation preserved
    loaded = load(sess.target, sid)
    assert loaded.config.budget == 20.0
    assert len(loaded.conversation) == 2
    assert loaded.stats.total_cost == 0.03
    assert loaded.stats.turns == 2


def test_update_max_turns_preserves_conversation(tmp_path, monkeypatch):
    """Regression: /turns should mutate in place — same shape as /budget."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession, Exchange
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
        budget=5.0,
        max_turns=50,
    )
    sid = sess._snapshot.session_id

    sess.exchanges.append(Exchange(
        user="ping", agent="pong", turn=1,
        timestamp="2026-05-12T10:00:00", cost=0.01,
    ))
    sess.stats.total_cost = 0.01
    sess.stats.turns = 1
    sess._autosave_snapshot()

    sess.update_max_turns(100)

    assert sess.max_turns == 100
    assert sess.stats.max_turns == 100
    assert sess._snapshot.config.max_turns == 100

    assert len(sess.exchanges) == 1
    assert sess.exchanges[0].user == "ping"

    loaded = load(sess.target, sid)
    assert loaded.config.max_turns == 100
    assert len(loaded.conversation) == 1

    # Regression: updating the cap is NOT session completion. The session log
    # must not contain a `session_completed` event from this call.
    import json
    sess._slog._f.flush()
    log_lines = open(sess._log_path).read().strip().split("\n")
    types = [json.loads(line)["type"] for line in log_lines if line]
    assert "session_completed" not in types
