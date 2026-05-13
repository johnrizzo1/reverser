"""SessionManager owns the active GUISession + lists historical snapshots."""
from pathlib import Path
from unittest.mock import patch

import pytest

from reverser.gui_service.event_bus import EventBus
from reverser.gui_service.session_manager import SessionManager
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bus = EventBus()
    return SessionManager(bus=bus, targets_root=tmp_path)


@pytest.mark.asyncio
async def test_create_session_returns_id_and_marks_active(manager, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        info = await manager.create_session(
            target=str(tmp_path / "bin"),
            profile_key="general",
            backend_name="claude",
            model=None,
            api_base=None,
            budget=5.0,
            max_turns=50,
        )
    assert info["id"]
    assert info["state"] == "active"
    assert manager.active is not None
    assert manager.active.session_id == info["id"]


@pytest.mark.asyncio
async def test_only_one_active_session_at_a_time(manager, tmp_path):
    """Creating a new session while another is active stops the first."""
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        a = await manager.create_session(
            target=str(tmp_path / "a"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
        b = await manager.create_session(
            target=str(tmp_path / "b"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
    assert a["id"] != b["id"]
    assert manager.active is not None
    assert manager.active.session_id == b["id"]
    # The first session was transitioned to "stopped" before the new one started.


@pytest.mark.asyncio
async def test_list_sessions_includes_active(manager, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        info = await manager.create_session(
            target=str(tmp_path / "bin"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
    sessions = manager.list_sessions()
    assert any(s["id"] == info["id"] and s["state"] == "active" for s in sessions)


def test_pentest_authorization_required_for_network_profile(manager, tmp_path, monkeypatch):
    monkeypatch.delenv("REVERSER_PENTEST_AUTHORIZED", raising=False)
    # No .reverser-authorized either; manager scans CWD which is tmp_path
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PermissionError):
        # webpentest is a network-touching profile
        import asyncio
        asyncio.run(manager.create_session(
            target="https://example.com",
            profile_key="webpentest",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        ))


@pytest.mark.asyncio
async def test_list_sessions_active_row_has_profile_key(manager, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        await manager.create_session(
            target=str(tmp_path / "bin"),
            profile_key="general",
            backend_name="claude", model=None, api_base=None,
            budget=5.0, max_turns=50,
        )
    rows = manager.list_sessions()
    for row in rows:
        assert "profile" in row
        assert "profile_key" not in row  # should be normalized to "profile"
