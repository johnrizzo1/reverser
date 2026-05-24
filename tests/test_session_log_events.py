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


def test_session_log_appends_across_reopens(tmp_path):
    """Regression: opening SessionLog for an existing path must not wipe
    prior events. Resume reopens the same log path; before this fix it
    truncated and the prior session's events were lost forever — which is
    why /api/sessions/log/{id} only returned `session_resumed` for any
    snapshot that had been resumed at least once.
    """
    from reverser.session_log import SessionLog, load_session_log

    log_path = tmp_path / "rolling.jsonl"
    a = SessionLog(str(log_path))
    a.log_turn(1)
    a.log_text("hello from session A", turn=1)
    a.close()

    # Simulate resume: reopen the same path.
    b = SessionLog(str(log_path))
    b.log_session_resumed(session_id="s1", prior_turn=1, prior_cost=0.0)
    b.log_turn(2)
    b.close()

    entries = load_session_log(str(log_path))
    kinds = [e["type"] for e in entries]
    assert kinds == ["turn", "text", "session_resumed", "turn"]
