"""Tests for the new SessionLog event kinds."""

import json


def test_log_session_resumed_writes_event(tmp_path):
    from reverser.session_log import SessionLog

    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    slog.log_session_resumed(
        session_id="2026-05-09T14-23-00",
        prior_turn=42,
        prior_cost=1.84,
    )
    slog.close()

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "session_resumed"
    assert event["session_id"] == "2026-05-09T14-23-00"
    assert event["prior_turn"] == 42
    assert event["prior_cost"] == 1.84


def test_log_session_stopped_writes_event(tmp_path):
    from reverser.session_log import SessionLog

    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    slog.log_session_stopped(cost=2.50, turns=42)
    slog.close()

    event = json.loads(log_path.read_text().strip().split("\n")[-1])
    assert event["type"] == "session_stopped"
    assert event["cost"] == 2.50
    assert event["turns"] == 42


def test_log_session_completed_writes_event(tmp_path):
    from reverser.session_log import SessionLog

    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    slog.log_session_completed(cost=3.75, turns=55)
    slog.close()

    event = json.loads(log_path.read_text().strip().split("\n")[-1])
    assert event["type"] == "session_completed"
    assert event["cost"] == 3.75
    assert event["turns"] == 55
