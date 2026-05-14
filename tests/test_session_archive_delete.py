"""Helper coverage for archive/delete on SessionSnapshot."""
from __future__ import annotations

import pytest


def test_archived_at_defaults_to_none(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import SessionConfig, new_snapshot

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    assert snap.archived_at is None


def test_archived_at_round_trips_through_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig,
        SessionSnapshot,
        load,
        save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-14T10-00-00",
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        state="stopped",
        started_at="2026-05-14T10:00:00",
        last_active_at="2026-05-14T10:00:00",
        config=SessionConfig(profile="manager"),
        archived_at="2026-05-14T11:00:00+00:00",
    )
    save(snap)
    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.archived_at == "2026-05-14T11:00:00+00:00"


def test_set_archived_writes_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, load, new_snapshot, save, set_archived,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    snap.state = "stopped"
    save(snap)

    set_archived("10.10.10.5", snap.session_id, True)
    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.archived_at is not None
    assert reloaded.archived_at.startswith("20")  # looks like an ISO timestamp


def test_set_archived_false_clears_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, load, new_snapshot, save, set_archived,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    snap.state = "stopped"
    save(snap)
    set_archived("10.10.10.5", snap.session_id, True)
    set_archived("10.10.10.5", snap.session_id, False)
    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.archived_at is None


def test_delete_unlinks_snapshot_and_log(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from reverser.sessions import (
        SessionConfig, SessionNotFoundError, delete, load,
        new_snapshot, save, snapshot_path,
    )

    log_path = tmp_path / "logs" / "x.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("event\n")

    snap = new_snapshot(
        target="10.10.10.5",
        log_path=str(log_path),
        config=SessionConfig(profile="manager"),
    )
    snap.state = "stopped"  # not active
    save(snap)

    delete("10.10.10.5", snap.session_id)

    assert not snapshot_path("10.10.10.5", snap.session_id).exists()
    assert not log_path.exists()
    with pytest.raises(SessionNotFoundError):
        load("10.10.10.5", snap.session_id)


def test_delete_is_ok_when_log_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, delete, new_snapshot, save, snapshot_path,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path=str(tmp_path / "logs" / "missing.jsonl"),  # never created
        config=SessionConfig(profile="manager"),
    )
    snap.state = "stopped"
    save(snap)
    # Should not raise even though the log doesn't exist
    delete("10.10.10.5", snap.session_id)
    assert not snapshot_path("10.10.10.5", snap.session_id).exists()


def test_delete_raises_on_active_session(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, SessionStateError, delete, new_snapshot, save,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    # new_snapshot sets state="active" by default
    save(snap)
    with pytest.raises(SessionStateError):
        delete("10.10.10.5", snap.session_id)


def test_set_archived_raises_on_active_session(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.sessions import (
        SessionConfig, SessionStateError, new_snapshot, save, set_archived,
    )

    snap = new_snapshot(
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        config=SessionConfig(profile="manager"),
    )
    save(snap)  # state == "active"
    with pytest.raises(SessionStateError):
        set_archived("10.10.10.5", snap.session_id, True)
