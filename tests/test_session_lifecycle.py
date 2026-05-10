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
