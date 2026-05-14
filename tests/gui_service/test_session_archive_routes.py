"""Tests for archive/unarchive/hard-delete on session snapshots."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from reverser.sessions import (
    SessionConfig, SessionSnapshot, save,
)
from tests.gui_service.fakes import FakeBackend


HEADERS = {"Authorization": "Bearer t"}


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(host="127.0.0.1", port=0, token="t",
                         project_root=str(tmp_path))


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


def _persist_stopped_snapshot(tmp_path, target="10.10.10.5",
                              sid="2026-05-14T10-00-00"):
    """Write a stopped snapshot directly to disk (bypasses SessionManager)."""
    log = tmp_path / "logs" / f"{sid}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("event\n")
    snap = SessionSnapshot(
        session_id=sid, target=target, log_path=str(log),
        state="stopped",
        started_at="2026-05-14T10:00:00", last_active_at="2026-05-14T10:00:00",
        config=SessionConfig(profile="manager"),
    )
    save(snap)
    return snap


@pytest.mark.asyncio
async def test_archive_session_204(client, tmp_path):
    snap = _persist_stopped_snapshot(tmp_path)
    r = await client.post(
        f"/api/sessions/{snap.session_id}/archive?target={snap.target}",
        headers=HEADERS,
    )
    assert r.status_code == 204, r.text
    rows = (await client.get("/api/sessions", headers=HEADERS)).json()["sessions"]
    row = next(x for x in rows if x["id"] == snap.session_id)
    assert row["archived_at"] is not None


@pytest.mark.asyncio
async def test_unarchive_session_204(client, tmp_path):
    snap = _persist_stopped_snapshot(tmp_path)
    await client.post(
        f"/api/sessions/{snap.session_id}/archive?target={snap.target}",
        headers=HEADERS,
    )
    r = await client.delete(
        f"/api/sessions/{snap.session_id}/archive?target={snap.target}",
        headers=HEADERS,
    )
    assert r.status_code == 204
    rows = (await client.get("/api/sessions", headers=HEADERS)).json()["sessions"]
    row = next(x for x in rows if x["id"] == snap.session_id)
    assert row["archived_at"] is None


@pytest.mark.asyncio
async def test_delete_session_204_removes_files(client, tmp_path):
    snap = _persist_stopped_snapshot(tmp_path)
    log_path = tmp_path / "logs" / f"{snap.session_id}.jsonl"
    snap_file = (tmp_path / "targets" / snap.target / "sessions" /
                 f"{snap.session_id}.json")
    assert log_path.exists()
    assert snap_file.exists()

    r = await client.delete(
        f"/api/sessions/{snap.session_id}?target={snap.target}",
        headers=HEADERS,
    )
    assert r.status_code == 204
    assert not log_path.exists()
    assert not snap_file.exists()


@pytest.mark.asyncio
async def test_archive_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        target = create.json()["target"]

    r = await client.post(
        f"/api/sessions/{sid}/archive?target={target}",
        headers=HEADERS,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "bin"), "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        target = create.json()["target"]

    r = await client.delete(
        f"/api/sessions/{sid}?target={target}",
        headers=HEADERS,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_archive_missing_session_returns_404(client):
    r = await client.post(
        "/api/sessions/nope/archive?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_session_returns_404(client):
    r = await client.delete(
        "/api/sessions/nope?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unarchive_missing_session_returns_404(client):
    r = await client.delete(
        "/api/sessions/nope/archive?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_sessions_includes_archived_at_field(client, tmp_path):
    """Every session row must include archived_at (null by default)."""
    _persist_stopped_snapshot(tmp_path)
    rows = (await client.get("/api/sessions", headers=HEADERS)).json()["sessions"]
    assert rows, "expected at least one row"
    for row in rows:
        assert "archived_at" in row, f"row missing archived_at: {row}"
