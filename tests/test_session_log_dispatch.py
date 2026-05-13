"""SessionLog persists dispatch_specialist sub-agent events.

The TUI surfaces dispatch events live via emit_dispatch_event; this
parallel write to the JSONL log lets read-only session replay show them
after the fact.
"""
import json

import pytest

from reverser.session_log import SessionLog, load_session_log


def test_log_dispatch_event_writes_expected_shape(tmp_path):
    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    try:
        slog.log_dispatch_event("ad", "tool_call", "ldap_search cn=Users,dc=corp")
    finally:
        slog.close()

    entries = load_session_log(str(log_path))
    dispatch_entries = [e for e in entries if e.get("type") == "dispatch"]
    assert len(dispatch_entries) == 1
    e = dispatch_entries[0]
    assert e["specialty"] == "ad"
    assert e["kind"] == "tool_call"
    assert e["content"] == "ldap_search cn=Users,dc=corp"
    assert "ts" in e


def test_log_dispatch_event_truncates_content(tmp_path):
    """Content is capped to keep log files manageable."""
    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    try:
        slog.log_dispatch_event("ad", "tool_result", "x" * 5000)
    finally:
        slog.close()

    entries = load_session_log(str(log_path))
    e = next(e for e in entries if e["type"] == "dispatch")
    assert len(e["content"]) <= 4096
