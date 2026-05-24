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
