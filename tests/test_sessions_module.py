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


def test_conversation_entry_events_default_empty():
    """Every entry carries an `events` list — the per-turn thinking /
    tool_call / tool_result records the resumed agent needs to know what
    happened previously. Defaults to [] so old code paths keep working."""
    e = ConversationEntry(
        user="hello", agent="hi", turn=1,
        timestamp="2026-05-09T14:23:00Z", cost=0.01,
    )
    assert e.events == []


def test_load_old_snapshot_without_events_field(tmp_path, monkeypatch):
    """Snapshots written before the events field existed must still load
    (the field defaults to []). Regression for the rich-context schema
    bump — without backward compat, all existing snapshots break."""
    import json
    from reverser.sessions import load, snapshot_path

    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    p = snapshot_path("10.10.10.5", "old-session")
    p.parent.mkdir(parents=True, exist_ok=True)
    # Hand-written old-shape entry: no `events` key.
    p.write_text(json.dumps({
        "session_id": "old-session",
        "target": "10.10.10.5",
        "log_path": "logs/old.jsonl",
        "state": "stopped",
        "started_at": "2026-05-09T14:23:00",
        "last_active_at": "2026-05-09T14:23:00",
        "config": {"profile": "general"},
        "stats": {"total_cost": 0.0, "turns": 1},
        "conversation": [
            {"user": "q", "agent": "a", "turn": 1,
             "timestamp": "2026-05-09T14:23:00", "cost": 0.01},
        ],
        "ui": {},
        "schema_version": 1,
    }))

    snap = load("10.10.10.5", "old-session")
    assert len(snap.conversation) == 1
    assert snap.conversation[0].events == []


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


def test_snapshot_path_uses_targets_dir(tmp_path, monkeypatch):
    """snapshot_path returns targets/<target>/sessions/<id>.json under REVERSER_TARGETS_DIR."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import snapshot_path

    p = snapshot_path("10.10.10.5", "2026-05-09T14-23-00")
    assert p == tmp_path / "10.10.10.5" / "sessions" / "2026-05-09T14-23-00.json"


def test_save_creates_directory_and_file(tmp_path, monkeypatch):
    """save() creates the target/sessions/ directory if missing and writes the file."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    expected = tmp_path / "10.10.10.5" / "sessions" / f"{snap.session_id}.json"
    assert expected.exists()
    import json
    data = json.loads(expected.read_text())
    assert data["session_id"] == snap.session_id
    assert data["state"] == "active"


def test_save_updates_last_active_at(tmp_path, monkeypatch):
    """save() bumps last_active_at to now before serializing."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, load, new_snapshot, SessionConfig
    import time

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    original_last_active = snap.last_active_at
    time.sleep(1.1)
    save(snap)

    loaded = load("10.10.10.5", snap.session_id)
    assert loaded.last_active_at != original_last_active


def test_save_is_atomic(tmp_path, monkeypatch):
    """save() never leaves a partially-written file at the canonical path."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    canonical = tmp_path / "10.10.10.5" / "sessions" / f"{snap.session_id}.json"
    tmp_files = list((tmp_path / "10.10.10.5" / "sessions").glob("*.tmp"))

    assert canonical.exists()
    assert tmp_files == []


def test_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, load, new_snapshot, SessionConfig, ConversationEntry,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager", budget=10.0),
    )
    snap.conversation = [
        ConversationEntry(user="hi", agent="hello", turn=1,
                          timestamp="2026-05-09T14:23:00", cost=0.01),
        ConversationEntry(user="next", agent="ok", turn=2,
                          timestamp="2026-05-09T14:24:00", cost=0.02),
    ]
    snap.stats.total_cost = 0.03
    snap.stats.turns = 2
    save(snap)

    loaded = load("10.10.10.5", snap.session_id)
    assert loaded.session_id == snap.session_id
    assert loaded.target == snap.target
    assert loaded.config.profile == "manager"
    assert loaded.config.budget == 10.0
    assert loaded.stats.total_cost == 0.03
    assert loaded.stats.turns == 2
    assert len(loaded.conversation) == 2
    assert loaded.conversation[0].user == "hi"
    assert loaded.conversation[1].cost == 0.02


def test_load_raises_session_not_found_on_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import load, SessionNotFoundError

    with pytest.raises(SessionNotFoundError):
        load("10.10.10.5", "nonexistent-session-id")


def test_load_raises_schema_error_on_bad_version(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import load, SchemaError, snapshot_path
    import json

    p = snapshot_path("10.10.10.5", "future-session")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "session_id": "future-session",
        "target": "10.10.10.5",
        "log_path": "logs/x.jsonl",
        "state": "active",
        "started_at": "2030-01-01T00:00:00",
        "last_active_at": "2030-01-01T00:00:00",
        "schema_version": 99,
    }))

    with pytest.raises(SchemaError):
        load("10.10.10.5", "future-session")


def test_save_overwrites_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, load, new_snapshot, SessionConfig

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)
    snap.stats.turns = 5
    save(snap)

    loaded = load("10.10.10.5", snap.session_id)
    assert loaded.stats.turns == 5


def test_list_for_target_returns_snapshots_sorted_desc(tmp_path, monkeypatch):
    """Sort by last_active_at desc.

    Note: save() bumps last_active_at to now, so we sleep between saves
    so the second snapshot has a strictly newer timestamp at second precision.
    """
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import time
    from reverser.sessions import (
        save, list_for_target, SessionSnapshot, SessionConfig,
    )
    older = SessionSnapshot(
        session_id="2026-05-08T09-12-44", target="10.10.10.5",
        log_path="logs/older.jsonl", state="stopped",
        started_at="2026-05-08T09:12:44", last_active_at="2026-05-08T17:33:00",
        config=SessionConfig(profile="manager"),
    )
    save(older)
    time.sleep(1.1)
    newer = SessionSnapshot(
        session_id="2026-05-09T14-23-00", target="10.10.10.5",
        log_path="logs/newer.jsonl", state="active",
        started_at="2026-05-09T14:23:00", last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="manager"),
    )
    save(newer)
    snaps = list_for_target("10.10.10.5")
    assert len(snaps) == 2
    assert snaps[0].session_id == "2026-05-09T14-23-00"
    assert snaps[1].session_id == "2026-05-08T09-12-44"


def test_list_for_target_empty_when_no_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import list_for_target
    assert list_for_target("nonexistent-target") == []


def test_list_for_target_excludes_completed_when_requested(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, list_for_target, SessionSnapshot, SessionConfig,
    )
    completed = SessionSnapshot(
        session_id="completed-1", target="10.10.10.5", log_path="logs/c.jsonl",
        state="completed", started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00", config=SessionConfig(profile="manager"),
    )
    active = SessionSnapshot(
        session_id="active-1", target="10.10.10.5", log_path="logs/a.jsonl",
        state="active", started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00", config=SessionConfig(profile="manager"),
    )
    save(completed)
    save(active)
    assert len(list_for_target("10.10.10.5")) == 2
    only_resumable = list_for_target("10.10.10.5", exclude_completed=True)
    assert len(only_resumable) == 1
    assert only_resumable[0].session_id == "active-1"


def test_list_for_target_skips_tmp_files(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, list_for_target, snapshot_path, new_snapshot, SessionConfig
    snap = new_snapshot(target="10.10.10.5", log_path="logs/x.jsonl",
                        config=SessionConfig(profile="manager"))
    save(snap)
    orphan = snapshot_path("10.10.10.5", "orphan-id").with_suffix(".json.tmp")
    orphan.write_text("{not even valid json")
    snaps = list_for_target("10.10.10.5")
    assert len(snaps) == 1


def test_list_all_walks_all_target_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, list_all, SessionSnapshot, SessionConfig
    snap_a = SessionSnapshot(
        session_id="2026-05-09T14-23-00", target="10.10.10.5", log_path="logs/a.jsonl",
        state="active", started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00", config=SessionConfig(profile="manager"),
    )
    snap_b = SessionSnapshot(
        session_id="2026-05-08T09-12-44", target="10.10.10.7", log_path="logs/b.jsonl",
        state="stopped", started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00", config=SessionConfig(profile="ad"),
    )
    save(snap_a)
    save(snap_b)
    snaps = list_all()
    assert len(snaps) == 2
    assert {s.target for s in snaps} == {"10.10.10.5", "10.10.10.7"}


def test_list_all_skips_dot_prefixed_directories(tmp_path, monkeypatch):
    """list_all must skip .trash/ and other hidden dirs without crashing."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, SessionSnapshot, list_all, save,
    )

    # Seed a real target with one session
    snap = SessionSnapshot(
        session_id="real-session", target="10.10.10.5",
        log_path="logs/x.jsonl", state="stopped",
        started_at="2026-05-14T10:00:00", last_active_at="2026-05-14T10:00:00",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    # Create a .trash directory that would parse but should be skipped
    (tmp_path / ".trash").mkdir()
    (tmp_path / ".trash" / "sessions").mkdir()

    snaps = list_all()
    assert len(snaps) == 1
    assert snaps[0].session_id == "real-session"


def test_latest_for_target_picks_most_recent_resumable(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, latest_for_target, SessionSnapshot, SessionConfig
    import time
    older = SessionSnapshot(
        session_id="older", target="10.10.10.5", log_path="logs/o.jsonl",
        state="stopped", started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00", config=SessionConfig(profile="manager"),
    )
    save(older)
    time.sleep(1.1)
    newer = SessionSnapshot(
        session_id="newer", target="10.10.10.5", log_path="logs/n.jsonl",
        state="active", started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00", config=SessionConfig(profile="manager"),
    )
    save(newer)
    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "newer"


def test_latest_for_target_excludes_completed_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, latest_for_target, SessionSnapshot, SessionConfig
    completed = SessionSnapshot(
        session_id="completed-recent", target="10.10.10.5", log_path="logs/c.jsonl",
        state="completed", started_at="2026-05-09T15:00:00",
        last_active_at="2026-05-09T18:00:00", config=SessionConfig(profile="manager"),
    )
    older_stopped = SessionSnapshot(
        session_id="stopped-older", target="10.10.10.5", log_path="logs/s.jsonl",
        state="stopped", started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00", config=SessionConfig(profile="manager"),
    )
    save(completed)
    save(older_stopped)
    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "stopped-older"


def test_latest_for_target_returns_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import latest_for_target
    assert latest_for_target("nothing-here") is None


def test_latest_global_picks_most_recent_across_all_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import save, latest_global, SessionSnapshot, SessionConfig
    import time
    a = SessionSnapshot(
        session_id="a", target="10.10.10.5", log_path="logs/a.jsonl",
        state="stopped", started_at="2026-05-08T09:12:44",
        last_active_at="2026-05-08T17:33:00", config=SessionConfig(profile="manager"),
    )
    save(a)
    time.sleep(1.1)
    b = SessionSnapshot(
        session_id="b", target="10.10.10.7", log_path="logs/b.jsonl",
        state="active", started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00", config=SessionConfig(profile="ad"),
    )
    save(b)
    latest = latest_global()
    assert latest is not None
    assert latest.session_id == "b"
    assert latest.target == "10.10.10.7"


def test_is_session_alive_true_for_own_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import is_session_alive, new_snapshot, SessionConfig
    import os
    snap = new_snapshot(target="10.10.10.5", log_path="logs/x.jsonl",
                        config=SessionConfig(profile="manager"))
    assert snap.pid == os.getpid()
    assert is_session_alive(snap) is True


def test_is_session_alive_false_for_dead_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import is_session_alive, new_snapshot, SessionConfig
    snap = new_snapshot(target="10.10.10.5", log_path="logs/x.jsonl",
                        config=SessionConfig(profile="manager"))
    snap.pid = 999999
    assert is_session_alive(snap) is False


def test_is_session_alive_false_when_pid_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import is_session_alive, new_snapshot, SessionConfig
    snap = new_snapshot(target="10.10.10.5", log_path="logs/x.jsonl",
                        config=SessionConfig(profile="manager"))
    snap.pid = None
    assert is_session_alive(snap) is False


def test_latest_for_target_prefers_nonempty_session(tmp_path, monkeypatch):
    """latest_for_target picks the most recent session with turns>0 over a
    more-recent empty session."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import time
    from reverser.sessions import (
        save, latest_for_target, SessionSnapshot, SessionConfig, SessionStats,
    )
    # Older session with real work
    with_work = SessionSnapshot(
        session_id="2026-05-10T17-52-25", target="10.10.10.5",
        log_path="logs/work.jsonl", state="active",
        started_at="2026-05-10T17:52:25", last_active_at="2026-05-10T17:52:25",
        config=SessionConfig(profile="manager"),
        stats=SessionStats(turns=50, total_cost=1.84),
    )
    save(with_work)
    time.sleep(1.1)
    # Newer empty session (e.g. accidental launch)
    empty = SessionSnapshot(
        session_id="2026-05-10T19-05-07", target="10.10.10.5",
        log_path="logs/empty.jsonl", state="active",
        started_at="2026-05-10T19:05:07", last_active_at="2026-05-10T19:05:07",
        config=SessionConfig(profile="manager"),
        stats=SessionStats(turns=0, total_cost=0.0),
    )
    save(empty)

    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "2026-05-10T17-52-25"
    assert latest.stats.turns == 50


def test_latest_for_target_falls_back_when_all_empty(tmp_path, monkeypatch):
    """When every eligible session has turns==0, return the most recent
    rather than None."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import time
    from reverser.sessions import (
        save, latest_for_target, SessionSnapshot, SessionConfig,
    )
    older = SessionSnapshot(
        session_id="older", target="10.10.10.5", log_path="logs/o.jsonl",
        state="active", started_at="2026-05-10T17:00:00",
        last_active_at="2026-05-10T17:00:00",
        config=SessionConfig(profile="general"),
    )
    save(older)
    time.sleep(1.1)
    newer = SessionSnapshot(
        session_id="newer", target="10.10.10.5", log_path="logs/n.jsonl",
        state="active", started_at="2026-05-10T18:00:00",
        last_active_at="2026-05-10T18:00:00",
        config=SessionConfig(profile="general"),
    )
    save(newer)

    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "newer"  # all empty, falls back to most recent


def test_latest_for_target_excludes_abandoned_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, latest_for_target, SessionSnapshot, SessionConfig, SessionStats,
    )
    abandoned_recent = SessionSnapshot(
        session_id="abandoned-1", target="10.10.10.5",
        log_path="logs/a.jsonl", state="abandoned",
        started_at="2026-05-10T19:00:00", last_active_at="2026-05-10T19:00:00",
        config=SessionConfig(profile="general"),
    )
    stopped_older = SessionSnapshot(
        session_id="stopped-1", target="10.10.10.5",
        log_path="logs/s.jsonl", state="stopped",
        started_at="2026-05-10T17:00:00", last_active_at="2026-05-10T17:00:00",
        config=SessionConfig(profile="general"),
        stats=SessionStats(turns=10),
    )
    save(abandoned_recent)
    save(stopped_older)

    latest = latest_for_target("10.10.10.5")
    assert latest is not None
    assert latest.session_id == "stopped-1"


def test_abandoned_state_round_trip(tmp_path, monkeypatch):
    """A snapshot with state='abandoned' loads back correctly."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        save, load, SessionSnapshot, SessionConfig,
    )
    snap = SessionSnapshot(
        session_id="abandoned-1", target="10.10.10.5",
        log_path="logs/a.jsonl", state="abandoned",
        started_at="2026-05-10T19:00:00", last_active_at="2026-05-10T19:00:00",
        config=SessionConfig(profile="general"),
    )
    save(snap)
    loaded = load("10.10.10.5", "abandoned-1")
    assert loaded.state == "abandoned"
