"""Tests for the KB → WS frame bridge."""
from unittest.mock import MagicMock

from reverser.gui_service.kb_emitter import (
    emit_hypothesis,
    emit_finding,
    emit_recorded_finding,
)
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


def test_emit_recorded_finding_includes_id(monkeypatch):
    sess = MagicMock()
    sess.emit_kb_event = MagicMock()
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = FindingFact(title="weak tls", severity="medium", description="TLS 1.0")
        emit_recorded_finding("create", 42, row)
    finally:
        current_session.reset(token)
    sess.emit_kb_event.assert_called_once()
    args = sess.emit_kb_event.call_args.args
    assert args[0] == "finding"
    assert args[1]["action"] == "create"
    assert args[1]["row"]["id"] == 42
    assert args[1]["row"]["title"] == "weak tls"


def test_kb_store_writes_emit_generic_kb_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser.sessions import current_session
    from reverser.kb import (
        ArtifactFact,
        CredentialFact,
        HostFact,
        ServiceFact,
        for_target,
    )

    sess = MagicMock()
    sess.emit_kb_event = MagicMock()
    token = current_session.set(sess)
    try:
        kb = for_target("10.10.10.5")
        kb.record_host(HostFact(ip="10.10.10.5"))
        kb.record_service(ServiceFact(host_ip="10.10.10.5", port=445, proto="tcp"))
        kb.record_credential(CredentialFact(username="alice", password="pw"))
        kb.record_artifact(ArtifactFact(
            kind="loot",
            path="loot/hash.txt",
            sha256="a" * 64,
            source_tool="test",
        ))
    finally:
        current_session.reset(token)

    calls = [call.args for call in sess.emit_kb_event.call_args_list]
    assert ("kb", {"target": "10.10.10.5", "tables": ["hosts"]}) in calls
    assert ("kb", {"target": "10.10.10.5", "tables": ["services"]}) in calls
    assert ("kb", {"target": "10.10.10.5", "tables": ["credentials"]}) in calls
    assert ("kb", {"target": "10.10.10.5", "tables": ["artifacts"]}) in calls


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


@pytest.mark.asyncio
async def test_kb_add_finding_emits_frame(session_with_spy, tmp_path, monkeypatch):
    """The kb_add_finding tool publishes a finding frame after the write."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser.tools.kb import kb_add_finding
    from reverser.kb import for_target
    target = "test-target"
    for_target(target)
    monkeypatch.setattr("reverser.tools.kb._check_auth", lambda: None)

    fn = getattr(kb_add_finding, "handler", None) or kb_add_finding
    await fn({
        "target": target,
        "title": "SMB signing not required",
        "severity": "medium",
        "description": "Allows relay.",
        "evidence_paths": ["findings/smb.txt"],
        "reproduction": "Run responder.",
        "confidence": 80,
        "reachability": "demonstrated",
    })

    session_with_spy.on_kb_event.assert_called()
    kind, payload = session_with_spy.on_kb_event.call_args.args
    assert kind == "finding"
    assert payload["action"] == "create"
    assert payload["row"]["id"] == 1
    assert payload["row"]["title"] == "SMB signing not required"
