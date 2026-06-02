"""Tests for resuming an AgentSession from a SessionSnapshot."""

import os
import pytest


def test_session_init_new_creates_snapshot_on_disk(tmp_path, monkeypatch):
    """Constructing a fresh AgentSession creates a snapshot file at active state."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import snapshot_path

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("general"),
    )
    p = snapshot_path(sess.target, sess._snapshot.session_id)
    assert p.exists()
    assert sess._snapshot.state == "active"
    assert sess._snapshot.pid == os.getpid()


def test_session_init_resumed_restores_state_from_snapshot(tmp_path, monkeypatch):
    """Resuming from a snapshot restores all the operator state."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, SessionStats, ConversationEntry, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general", budget=10.0, max_turns=100),
        stats=SessionStats(total_cost=2.50, turns=42),
        conversation=[
            ConversationEntry(user="q1", agent="a1", turn=1,
                              timestamp="2026-05-09T14:23:00", cost=0.05),
            ConversationEntry(user="q2", agent="a2", turn=2,
                              timestamp="2026-05-09T14:24:00", cost=0.07),
        ],
    )
    save(snap)

    sess = AgentSession(
        binary_path="ignored-on-resume",
        profile=get_profile("general"),
        resume_from=snap,
    )

    assert sess.target == "10.10.10.5"
    assert sess.budget == 10.0
    assert sess.max_turns == 100
    assert sess.stats.total_cost == 2.50
    assert sess.stats.turns == 42
    assert len(sess.exchanges) == 2
    assert sess.exchanges[0].user == "q1"
    assert sess.exchanges[1].cost == 0.07
    # State flipped to active; pid is now ours
    assert sess._snapshot.state == "active"
    assert sess._snapshot.pid == os.getpid()


def test_session_init_resumed_clears_in_flight_dispatch(tmp_path, monkeypatch):
    """A dispatch in flight when the prior process exited must be cleared on
    resume — nothing is actually running, so it must not look active."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, SessionStats, InFlightDispatch, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general", budget=10.0, max_turns=100),
        stats=SessionStats(total_cost=0.0, turns=1),
        conversation=[],
        in_flight=InFlightDispatch(
            kind="dispatch", specialty="pentest", hypothesis_id=1,
            sub_goal="enum dirs", started_at="2026-05-09T18:46:00",
        ),
    )
    save(snap)

    sess = AgentSession(
        binary_path="ignored-on-resume",
        profile=get_profile("general"),
        resume_from=snap,
    )

    assert sess._snapshot.in_flight is None


def test_session_init_resumed_continues_writing_to_same_log(tmp_path, monkeypatch):
    """The resumed session reuses the snapshot's log_path; doesn't mint a new one."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/specific-log.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general"),
    )
    save(snap)

    sess = AgentSession(
        binary_path="ignored-on-resume",
        profile=get_profile("general"),
        resume_from=snap,
    )
    assert sess.log_path == "logs/specific-log.jsonl"


def test_session_init_resumed_rejects_profile_mismatch(tmp_path, monkeypatch):
    """If the caller passes a profile that doesn't match the snapshot, error."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import (
        SessionSnapshot, SessionConfig, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-09T14-23-00",
        target="10.10.10.5",
        log_path="logs/test.jsonl",
        state="stopped",
        started_at="2026-05-09T14:23:00",
        last_active_at="2026-05-09T18:47:00",
        config=SessionConfig(profile="general"),
    )
    save(snap)

    with pytest.raises(ValueError, match="profile"):
        AgentSession(
            binary_path="10.10.10.5",
            profile=get_profile("ad"),  # different profile
            resume_from=snap,
        )


def test_session_refocus_address_updates_target_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.targets import create_target, add_address, load_target

    create_target(name="box", kind="network", initial_address="10.0.0.1")
    sess = AgentSession.from_target(load_target("box"), profile=get_profile("pentest"))

    t = add_address(load_target("box"), "10.0.0.2", "ip", make_primary=True)
    new_addr = t.primary_address

    note = sess.refocus_address(new_addr)
    assert sess.target == "10.0.0.2"
    assert sess.active_address.value == "10.0.0.2"
    assert sess._snapshot.active_address_id == new_addr.id
    assert "10.0.0.2" in note
    # target_obj is refreshed from disk (not dropped) when the new address
    # wasn't in the session's startup copy of the target.
    assert sess.target_obj is not None
    assert sess.target_obj.primary_address.value == "10.0.0.2"


def test_resume_after_refocus_stays_on_new_ip(tmp_path, monkeypatch):
    """A session refocused to a new IP, then resumed from disk, must come back
    on the NEW IP — not revert to the old one."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.targets import create_target, add_address, load_target

    create_target(name="box", kind="network", initial_address="10.0.0.1")
    sess = AgentSession.from_target(load_target("box"), profile=get_profile("pentest"))

    t = add_address(load_target("box"), "10.0.0.2", "ip", make_primary=True)
    sess.refocus_address(t.primary_address)
    snap = sess._snapshot
    sess.close()

    # the refocus must have persisted the new IP onto the snapshot (the field
    # _init_resumed restores from) — otherwise a resume reverts to the old IP
    assert snap.target == "10.0.0.2"
    snap.state = "stopped"  # simulate a clean stop before resume
    resumed = AgentSession(
        binary_path="ignored-on-resume",
        profile=get_profile("pentest"),
        resume_from=snap,
    )
    assert resumed.target == "10.0.0.2"


def test_session_config_validation_fields_round_trip():
    from reverser.sessions import SessionConfig
    from dataclasses import asdict
    c = SessionConfig(profile="ad", validation_backend="claude",
                      validation_model="m", validation_api_base="http://x/v1")
    c2 = SessionConfig(**asdict(c))
    assert c2.validation_backend == "claude" and c2.validation_model == "m"
    assert c2.validation_api_base == "http://x/v1"


def test_session_config_validation_defaults_none():
    from reverser.sessions import SessionConfig
    assert SessionConfig(profile="general").validation_backend is None
    c2 = SessionConfig(profile="general", backend="claude", model=None, api_base=None,
                       budget=5.0, max_turns=50, max_parallel=1)
    assert c2.validation_backend is None


def test_session_config_token_cost_round_trip():
    from reverser.sessions import SessionConfig
    from dataclasses import asdict
    c = SessionConfig(profile="general", token_cost_per_1k=0.75)
    assert SessionConfig(**asdict(c)).token_cost_per_1k == 0.75


def test_session_config_token_cost_default_zero():
    from reverser.sessions import SessionConfig
    assert SessionConfig(profile="general").token_cost_per_1k == 0.0
    c = SessionConfig(profile="general", backend="claude", model=None, api_base=None,
                      budget=5.0, max_turns=50, max_parallel=1)
    assert c.token_cost_per_1k == 0.0
