"""Tests for the atexit / SIGTERM emergency snapshot hook."""


def test_emergency_snapshot_writes_when_session_present(tmp_path, monkeypatch):
    """The emergency_snapshot helper writes the current session's snapshot."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tui.app import _emergency_snapshot
    from reverser.sessions import load

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    sid = sess._snapshot.session_id
    sess.stats.turns = 7
    sess.stats.total_cost = 0.42
    # Update snapshot's stats so emergency save reflects them
    sess._snapshot.stats.turns = 7
    sess._snapshot.stats.total_cost = 0.42

    _emergency_snapshot(sess)

    loaded = load(sess.target, sid)
    assert loaded.stats.turns == 7
    assert loaded.stats.total_cost == 0.42


def test_emergency_snapshot_handles_none_session():
    """Called with None session, _emergency_snapshot is a no-op (no exception)."""
    from reverser.tui.app import _emergency_snapshot
    _emergency_snapshot(None)  # should not raise
