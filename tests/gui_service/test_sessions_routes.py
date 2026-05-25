"""Routes covering the active-engagement lifecycle.

POST /api/sessions creates an engagement.
GET /api/sessions lists.
POST /api/sessions/{id}/messages sends user input.
POST /api/sessions/{id}/skills/{key} triggers a skill.
POST /api/sessions/{id}/stop|done|resume changes lifecycle.
POST /api/sessions/{id}/budget updates caps.
POST /api/sessions/{id}/sudo stores in-memory.
"""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    # Direct sessions.py to write under tmp_path so each test is isolated
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_create_session_returns_id(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        r = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"),
            "profile": "general",
            "backend": "claude",
            "model": None,
            "api_base": None,
            "budget": 5.0,
            "max_turns": 50,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body
    assert body["state"] == "active"


@pytest.mark.asyncio
async def test_list_sessions_returns_active(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        listing = await client.get("/api/sessions", headers=HEADERS)
    assert listing.status_code == 200
    rows = listing.json()["sessions"]
    assert any(r["id"] == sid and r["state"] == "active" for r in rows)


@pytest.mark.asyncio
async def test_send_message_204(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/messages",
                              headers=HEADERS, json={"text": "hello"})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_stop_then_done(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        stop = await client.post(f"/api/sessions/{sid}/stop", headers=HEADERS)
    assert stop.status_code == 204


@pytest.mark.asyncio
async def test_stop_clears_active_so_list_reports_stopped(client, tmp_path):
    """Regression: POST /stop must clear manager.active. Otherwise
    list_sessions() overrides state to "active" and the UI's status
    indicator never changes."""
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        stop = await client.post(f"/api/sessions/{sid}/stop", headers=HEADERS)
        assert stop.status_code == 204

        listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["state"] == "stopped", (
        f"after POST /stop, list_sessions should report 'stopped' but "
        f"reported {row['state']!r} (manager.active not cleared)"
    )


@pytest.mark.asyncio
async def test_stop_disk_only_for_stale_active_snapshot(client, tmp_path):
    """Regression: stop must work on a snapshot whose state-on-disk says
    "active" but has no live GUISession (the process is long gone — e.g.
    a crash, or an orphan left by an earlier test run). Without the
    disk-only fallback, the desktop UI's Stop button returns 404 and the
    user can't archive the row.
    """
    from reverser.sessions import (
        SessionConfig, new_snapshot, save as save_snapshot,
    )
    # Manually write an orphan "active" snapshot to the targets dir. No
    # live GUISession for it.
    target = str(tmp_path / "bin")
    snap = new_snapshot(
        target=target,
        log_path=str(tmp_path / "log.jsonl"),
        config=SessionConfig(
            profile="general", backend="claude",
            model=None, api_base=None, budget=5.0, max_turns=50,
        ),
        session_id="orphan-1",
    )
    snap.state = "active"
    snap.pid = 99999
    save_snapshot(snap)

    r = await client.post("/api/sessions/orphan-1/stop", headers=HEADERS)
    assert r.status_code == 204, r.text

    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == "orphan-1")
    assert row["state"] == "stopped"


@pytest.mark.asyncio
async def test_stop_unknown_session_returns_404(client):
    r = await client.post("/api/sessions/no-such-id/stop", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_mark_done_clears_active_so_list_reports_completed(client, tmp_path):
    """Regression: POST /done on an active session must clear manager.active
    so list_sessions reports 'completed', not 'active'."""
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        done = await client.post(f"/api/sessions/{sid}/done", headers=HEADERS)
        assert done.status_code == 204

        listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["state"] == "completed", (
        f"after POST /done, list_sessions should report 'completed' but "
        f"reported {row['state']!r} (manager.active not cleared)"
    )


@pytest.mark.asyncio
async def test_budget_update(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/budget",
                              headers=HEADERS, json={"budget": 10.0, "max_turns": 100})
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_sudo_in_memory_only(client, tmp_path):
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/sudo",
                              headers=HEADERS, json={"password": "shhh"})
    assert r.status_code == 204
    # password must NOT appear in the snapshot on disk
    import os
    for root, _dirs, files in os.walk(str(tmp_path)):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert b"shhh" not in fh.read()


@pytest.mark.asyncio
async def test_sudo_makes_password_visible_to_tools(client, tmp_path):
    """Regression: GUISession.set_sudo must populate the module-level
    `_sudo_password` in tools/_common.py so network tools (nmap, netexec)
    can read it via get_sudo_password(). Before this fix, the GUI's Save
    button set an instance var and env var but left the tool-side
    store unchanged, so privileged scans still ran with no password.
    """
    from reverser.tools._common import get_sudo_password, set_sudo_password
    # Clear any prior state so we measure this test's effect.
    set_sudo_password(None)
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        r = await client.post(f"/api/sessions/{sid}/sudo",
                              headers=HEADERS, json={"password": "hunter2"})
    assert r.status_code == 204
    assert get_sudo_password() == "hunter2"
    # Clean up so we don't leak the value into other tests.
    set_sudo_password(None)


@pytest.mark.asyncio
async def test_sudo_disk_only_for_stale_active_snapshot(client, tmp_path):
    """Regression: POST /sudo must succeed when the snapshot is "active"
    on disk but no live GUISession exists in mgr.active. The SudoModal
    is rendered from the cached `useSessions` row's state, which can lag
    mgr.active by up to 5 s (post-stop/post-create) and can also report
    "active" for orphan snapshots after a backend restart. Before this
    fix, the user's Save click 404'd in those windows even though the
    password belongs on the process-wide tool store, not on any
    specific GUISession.
    """
    from reverser.sessions import (
        SessionConfig, new_snapshot, save as save_snapshot,
    )
    from reverser.tools._common import get_sudo_password, set_sudo_password
    set_sudo_password(None)

    target = str(tmp_path / "bin")
    snap = new_snapshot(
        target=target,
        log_path=str(tmp_path / "log.jsonl"),
        config=SessionConfig(
            profile="general", backend="claude",
            model=None, api_base=None, budget=5.0, max_turns=50,
        ),
        session_id="orphan-1",
    )
    snap.state = "active"
    snap.pid = 99999
    save_snapshot(snap)

    r = await client.post("/api/sessions/orphan-1/sudo",
                          headers=HEADERS, json={"password": "hunter2"})
    assert r.status_code == 204, r.text
    assert get_sudo_password() == "hunter2"
    set_sudo_password(None)


@pytest.mark.asyncio
async def test_sudo_unknown_session_returns_404(client):
    """The orphan-snapshot fallback must still reject session_ids that
    don't exist anywhere — otherwise a typo'd id would silently set the
    process-wide sudo password."""
    r = await client.post("/api/sessions/no-such-id/sudo",
                          headers=HEADERS, json={"password": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unknown_session_returns_404(client):
    r = await client.post("/api/sessions/missing/messages", headers=HEADERS, json={"text": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_mark_done_on_historical_snapshot_updates_state(client, tmp_path):
    """A historical session (no in-memory GUISession) can still be marked
    completed — the route falls back to a disk-only snapshot mutation."""
    from reverser.sessions import (
        SessionConfig, SessionSnapshot, load, save,
    )

    snap = SessionSnapshot(
        session_id="2026-05-10T17-52-25",
        target="10.10.10.5",
        log_path="logs/x.jsonl",
        state="stopped",
        started_at="2026-05-10T17:52:25",
        last_active_at="2026-05-10T17:52:25",
        config=SessionConfig(profile="manager"),
    )
    save(snap)

    r = await client.post(
        f"/api/sessions/{snap.session_id}/done", headers=HEADERS,
    )
    assert r.status_code == 204, r.text

    reloaded = load("10.10.10.5", snap.session_id)
    assert reloaded.state == "completed"


@pytest.mark.asyncio
async def test_mark_done_unknown_session_returns_404(client):
    r = await client.post("/api/sessions/does-not-exist/done", headers=HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Task 29: CreateSession accepts target_name + address override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_session_with_existing_target_name(client, tmp_path, monkeypatch):
    """POST /api/sessions with target_name resolves the named target and starts
    a session using its primary address value."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    # Pre-create a named target so resolve_target finds it by name.
    tmod.create_target("dc1", "network", "10.0.0.5")

    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        r = await client.post("/api/sessions", headers=HEADERS, json={
            "target_name": "dc1",
            "profile": "general",
            "backend": "claude",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id" in body
    assert body["state"] == "active"


@pytest.mark.asyncio
async def test_create_session_with_address_override(client, tmp_path, monkeypatch):
    """POST /api/sessions with target_name + address promotes the address to
    primary on the target before starting the session."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    t = tmod.create_target("dc1", "network", "10.0.0.5")
    original_primary = t.primary_address.value

    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        r = await client.post("/api/sessions", headers=HEADERS, json={
            "target_name": "dc1",
            "address": "10.0.0.99",
            "profile": "general",
            "backend": "claude",
        })
    assert r.status_code == 200, r.text

    # Verify the new address was added and is now primary on disk.
    reloaded = tmod.load_target("dc1")
    assert reloaded.primary_address.value == "10.0.0.99"
