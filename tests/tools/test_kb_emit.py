"""Tests for the KB → WS frame bridge."""
from unittest.mock import MagicMock

from reverser.gui_service.kb_emitter import emit_hypothesis, emit_finding
from reverser.kb.store import HypothesisFact, FindingFact


def test_emit_hypothesis_calls_session_callback(monkeypatch):
    sess = MagicMock()
    sess.emit_kb_event = MagicMock()
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = HypothesisFact(id=7, parent_id=None, statement="x", status="testing")
        emit_hypothesis("update", row)
    finally:
        current_session.reset(token)
    sess.emit_kb_event.assert_called_once()
    args = sess.emit_kb_event.call_args.args
    assert args[0] == "hypothesis"
    assert args[1]["action"] == "update"
    assert args[1]["row"]["id"] == 7


def test_emit_finding_calls_session_callback(monkeypatch):
    sess = MagicMock()
    sess.emit_kb_event = MagicMock()
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = FindingFact(title="open port", severity="low", description="ex.com port 80")
        emit_finding("create", row)
    finally:
        current_session.reset(token)
    sess.emit_kb_event.assert_called_once()
    args = sess.emit_kb_event.call_args.args
    assert args[0] == "finding"
    assert args[1]["action"] == "create"
    assert args[1]["row"]["title"] == "open port"


def test_emit_is_noop_when_no_session(monkeypatch):
    from reverser.sessions import current_session
    row = HypothesisFact(id=1, parent_id=None, statement="x", status="proposed")
    emit_hypothesis("create", row)  # must not raise


def test_emit_swallows_callback_exceptions():
    sess = MagicMock()
    sess.emit_kb_event = MagicMock(side_effect=RuntimeError("boom"))
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = HypothesisFact(id=1, parent_id=None, statement="x", status="proposed")
        emit_hypothesis("create", row)  # must not raise
    finally:
        current_session.reset(token)


import pytest


@pytest.fixture
def session_with_spy(tmp_path, monkeypatch):
    from reverser.sessions import current_session
    sess = MagicMock()
    sess.on_kb_event = MagicMock()
    sess.emit_kb_event = lambda kind, payload: sess.on_kb_event(kind, payload)
    token = current_session.set(sess)
    yield sess
    current_session.reset(token)


@pytest.mark.asyncio
async def test_kb_update_hypothesis_emits_frame(session_with_spy, tmp_path, monkeypatch):
    """The kb_update_hypothesis tool publishes a hypothesis frame after the write."""
    import os
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser.tools.kb import kb_update_hypothesis
    target = "test-target"
    from reverser.kb import for_target
    kb = for_target(target)
    h = kb.add_hypothesis(statement="x", parent_id=None)
    h_id = h.id
    monkeypatch.setattr("reverser.tools.kb._check_auth", lambda: None)

    fn = getattr(kb_update_hypothesis, "handler", None) or kb_update_hypothesis
    await fn({
        "target": target, "id": h_id, "status": "testing",
    })

    session_with_spy.on_kb_event.assert_called()
    kind, payload = session_with_spy.on_kb_event.call_args.args
    assert kind == "hypothesis"
    assert payload["action"] == "update"
    assert payload["row"]["id"] == h_id
    assert payload["row"]["status"] == "testing"
