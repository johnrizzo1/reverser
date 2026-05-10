"""Unit tests for the sessions module — snapshot dataclasses + helpers."""

import pytest

from reverser.sessions import (
    SessionSnapshot,
    SessionConfig,
    SessionStats,
    ConversationEntry,
    UIState,
    InFlightDispatch,
    make_session_id,
    new_snapshot,
)


def test_make_session_id_format():
    """Session IDs are filename-safe ISO timestamps."""
    sid = make_session_id()
    # Format: YYYY-MM-DDTHH-MM-SS (colons replaced with hyphens for filename safety)
    assert len(sid) == 19
    assert sid[10] == "T"
    assert sid[4] == "-" and sid[7] == "-"
    assert sid[13] == "-" and sid[16] == "-"
    # Verify filename safety
    assert ":" not in sid
    assert "/" not in sid


def test_session_config_defaults():
    """SessionConfig has sensible defaults requiring only profile."""
    c = SessionConfig(profile="general")
    assert c.profile == "general"
    assert c.backend == "claude"
    assert c.model is None
    assert c.api_base is None
    assert c.budget == 5.0
    assert c.max_turns == 50
    assert c.max_parallel == 1


def test_session_stats_defaults():
    s = SessionStats()
    assert s.total_cost == 0.0
    assert s.turns == 0


def test_conversation_entry_required_fields():
    e = ConversationEntry(
        user="hello",
        agent="hi",
        turn=1,
        timestamp="2026-05-09T14:23:00Z",
        cost=0.01,
    )
    assert e.user == "hello"
    assert e.cost == 0.01


def test_ui_state_defaults():
    u = UIState()
    assert u.focused_panel == "chat"
    assert u.chat_scroll_position == 0
    assert u.last_skill_key is None
    assert u.input_buffer == ""


def test_in_flight_dispatch_shape():
    f = InFlightDispatch(
        kind="dispatch",
        specialty="ad",
        hypothesis_id=5,
        sub_goal="Verify SMB signing",
        started_at="2026-05-09T14:23:00Z",
    )
    assert f.kind == "dispatch"
    assert f.hypothesis_id == 5


def test_new_snapshot_starts_active_with_pid(tmp_path, monkeypatch):
    """new_snapshot() returns a fresh SessionSnapshot with state=active and current pid."""
    import os
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    assert snap.session_id  # non-empty
    assert snap.target == "10.10.10.5"
    assert snap.log_path == "logs/test.jsonl"
    assert snap.state == "active"
    assert snap.config.profile == "manager"
    assert snap.pid == os.getpid()
    assert snap.started_at  # set
    assert snap.last_active_at  # set
    assert snap.stopped_at is None
    assert snap.in_flight is None
    assert snap.conversation == []
    assert snap.schema_version == 1


def test_snapshot_serializes_to_dict():
    """Snapshots round-trip through dataclasses.asdict for JSON encoding."""
    from dataclasses import asdict
    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="active",
        started_at="2026-05-09T14:23:00Z",
        last_active_at="2026-05-09T14:23:00Z",
        config=SessionConfig(profile="manager"),
    )
    d = asdict(snap)
    assert d["session_id"] == "2026-05-09T14-23-00"
    assert d["state"] == "active"
    assert d["config"]["profile"] == "manager"
    assert d["conversation"] == []
    assert d["pid"] is None
