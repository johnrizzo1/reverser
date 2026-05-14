"""Helper coverage for archive/delete on SessionSnapshot."""
from __future__ import annotations

from pathlib import Path

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
