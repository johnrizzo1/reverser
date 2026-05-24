"""AgentSession callback slots for KB and dispatch event bridging."""
import pytest
from unittest.mock import MagicMock
from reverser.agent_session import AgentSession
from reverser.profiles import get_profile


@pytest.fixture
def session(tmp_path):
    log = tmp_path / "log.jsonl"
    return AgentSession(
        binary_path=str(tmp_path / "noop"),
        profile=get_profile("general"),
        log_path=str(log),
    )


def test_emit_dispatch_event_with_id_and_sub_turn(session):
    spy = MagicMock()
    session.on_dispatch_event = spy
    session.emit_dispatch_event("webpentest", "abc-123", 2, "thinking", "hmm")
    spy.assert_called_once_with("webpentest", "abc-123", 2, "thinking", "hmm")


def test_emit_kb_event(session):
    spy = MagicMock()
    session.on_kb_event = spy
    session.emit_kb_event("hypothesis", {"action": "update", "row": {"id": 4}})
    spy.assert_called_once_with("hypothesis", {"action": "update", "row": {"id": 4}})


def test_kb_event_no_callback_is_safe(session):
    session.on_kb_event = None
    session.emit_kb_event("hypothesis", {"action": "update", "row": {"id": 4}})


def test_kb_event_callback_exception_swallowed(session):
    session.on_kb_event = MagicMock(side_effect=RuntimeError("ui crashed"))
    session.emit_kb_event("hypothesis", {})  # no raise
