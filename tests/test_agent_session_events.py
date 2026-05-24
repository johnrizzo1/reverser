"""Regression tests for AgentSession.send() exchange persistence.

Two failure modes the resume flow used to hit:
  1) Each Exchange only recorded a 2000-char text summary of the agent's
     reply — no thinking, tool calls, or tool results — so the resumed
     LLM couldn't see what tools it had already run.
  2) The exchange-append and snapshot autosave happened AFTER the
     try/finally in send(). On cancel (user clicks Stop), CancelledError
     propagated past finally and the partial exchange was lost entirely
     — even for sessions that had run many internal turns.
"""
from unittest.mock import patch

import pytest

from reverser.backends.base import AgentEvent
from tests.gui_service.fakes import FakeBackend


def _make_session(tmp_path, monkeypatch, fake_backend):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession

    with patch("reverser.agent_session.create_backend", return_value=fake_backend):
        return AgentSession(
            binary_path=str(tmp_path / "bin"),
            profile=get_profile("general"),
        )


@pytest.mark.asyncio
async def test_send_populates_exchange_events(tmp_path, monkeypatch):
    """A completed send() records the structured per-turn events
    (thinking/tool_call/tool_result/text) on the Exchange, so resume can
    replay them into the LLM's prompt."""
    fb = FakeBackend()
    fb.script = [
        AgentEvent(kind="turn", turns=1),
        AgentEvent(kind="thinking", content="picking a tool"),
        AgentEvent(
            kind="tool_call",
            tool_name="nmap_scan",
            tool_input='{"target":"10.0.0.1"}',
            tool_use_id="t1",
        ),
        AgentEvent(
            kind="tool_result",
            content="22/tcp open ssh",
            tool_use_id="t1",
            is_error=False,
        ),
        AgentEvent(kind="text", content="Found SSH on 22."),
        AgentEvent(kind="result", subtype="success", cost=0.01, turns=1),
    ]
    sess = _make_session(tmp_path, monkeypatch, fb)

    async for _ in sess.send("scan the host"):
        pass

    assert len(sess.exchanges) == 1
    events = sess.exchanges[0].events
    kinds = [e["kind"] for e in events]
    assert kinds == ["thinking", "tool_call", "tool_result", "text"]

    tc = events[1]
    assert tc["name"] == "nmap_scan"
    assert "10.0.0.1" in tc["args"]

    tr = events[2]
    assert tr["ok"] is True
    assert "22/tcp open" in tr["content"]


@pytest.mark.asyncio
async def test_send_persists_exchange_on_cancel(tmp_path, monkeypatch):
    """If the caller closes the send() generator before completion (i.e.
    the user stopped mid-turn), the partial exchange and its events must
    still be appended to self.exchanges and persisted to the snapshot.
    """
    fb = FakeBackend()
    fb.script = [
        AgentEvent(kind="turn", turns=1),
        AgentEvent(kind="text", content="partial reply before cancel"),
        AgentEvent(
            kind="tool_call",
            tool_name="nmap_scan",
            tool_input='{"target":"x"}',
            tool_use_id="t1",
        ),
        # Caller will aclose() before the result frame is reached.
        AgentEvent(kind="result", subtype="success", cost=0.0, turns=1),
    ]
    sess = _make_session(tmp_path, monkeypatch, fb)

    gen = sess.send("do a thing")
    # Pull the first two events, then close the generator (simulates cancel).
    await gen.__anext__()  # turn
    await gen.__anext__()  # text
    await gen.aclose()

    assert len(sess.exchanges) == 1, (
        "exchange must be appended even when send() is cancelled mid-stream"
    )
    assert "partial reply before cancel" in sess.exchanges[0].agent

    # And the snapshot on disk must reflect it.
    from reverser.sessions import load
    snap = load(sess.target, sess._snapshot.session_id)
    assert len(snap.conversation) == 1, (
        "snapshot autosave must run on cancel so resume sees the exchange"
    )
    assert snap.conversation[0].events, (
        "captured events must be persisted to the snapshot, not dropped"
    )


def test_build_prompt_includes_prior_tool_history(tmp_path, monkeypatch):
    """Resume context: when an exchange carries `events`, the next
    _build_prompt must surface the prior tool calls and results to the
    LLM — not just the agent's truncated text. Without this the resumed
    agent has no record of what it has already tried.
    """
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.agent_session import Exchange

    fb = FakeBackend()
    sess = _make_session(tmp_path, monkeypatch, fb)
    sess.exchanges = [
        Exchange(
            user="scan it", agent="found ssh",
            turn=1, timestamp="2026-05-24T00:00:00", cost=0.0,
            events=[
                {"kind": "tool_call", "name": "nmap_scan", "args": '{"target":"10.0.0.1"}'},
                {"kind": "tool_result", "ok": True, "content": "22/tcp open ssh"},
                {"kind": "text", "content": "Found SSH on 22."},
            ],
        ),
    ]
    # Avoid unused import warnings.
    assert get_profile
    assert AgentSession

    prompt = sess._build_prompt("what next?")
    assert "nmap_scan" in prompt, "prior tool call should appear in prompt"
    assert "22/tcp open ssh" in prompt, (
        "prior tool result should appear in prompt so the LLM doesn't "
        "rescan to rediscover state"
    )


def test_build_prompt_injects_resume_hint_once(tmp_path, monkeypatch):
    """After a resume, the first prompt must tell the LLM it's resuming
    and point it at the KB. The hint is one-shot — the second send()
    should NOT re-prefix it, otherwise the LLM gets confused on every
    user message after the first.
    """
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, SessionStats, save,
    )

    snap = SessionSnapshot(
        session_id="resume-1",
        target=str(tmp_path / "bin"),
        log_path=str(tmp_path / "log.jsonl"),
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general", budget=10.0, max_turns=100),
        stats=SessionStats(total_cost=0.4, turns=8),
    )
    save(snap)

    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        sess = AgentSession(
            binary_path="ignored-on-resume",
            profile=get_profile("general"),
            resume_from=snap,
        )

    first = sess._build_prompt("continue")
    assert "resuming" in first.lower(), (
        "first post-resume prompt must signal that the agent is resuming"
    )
    # Consume the one-shot flag the way send() does, then re-render.
    sess._first_send_pending = False
    second = sess._build_prompt("continue again")
    assert "resuming" not in second.lower(), (
        "resume hint must be one-shot — subsequent prompts get the normal "
        "context only"
    )


def test_build_prompt_advises_kb_on_fresh_session_start(tmp_path, monkeypatch):
    """The KB is per-target, not per-session — a *fresh* session against
    a target may still have prior hypotheses, findings, notes from
    earlier sessions. The first prompt on a brand-new session must
    advise the LLM to consult the KB before deciding what to do.
    """
    fb = FakeBackend()
    sess = _make_session(tmp_path, monkeypatch, fb)

    first = sess._build_prompt("kick off")
    lowered = first.lower()
    assert "kb_show" in first, (
        "fresh-session prompt should reference kb_show so the agent picks "
        "up any prior data for this target"
    )
    assert "kb_list_hypotheses" in first
    # Don't claim "resuming" on a fresh session — that would mislead the LLM.
    assert "resuming" not in lowered


def test_build_prompt_kb_advice_is_one_shot_for_fresh_session(tmp_path, monkeypatch):
    """Same one-shot semantics as the resume hint: the kickoff guidance
    appears only on the first prompt; subsequent prompts don't repeat it.
    """
    fb = FakeBackend()
    sess = _make_session(tmp_path, monkeypatch, fb)

    first = sess._build_prompt("kick off")
    assert "kb_show" in first

    sess._first_send_pending = False
    second = sess._build_prompt("next thing")
    assert "kb_show" not in second, (
        "kickoff advice must be one-shot, not repeated on every message"
    )
