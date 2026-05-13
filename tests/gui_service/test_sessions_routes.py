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
async def test_unknown_session_returns_404(client):
    r = await client.post("/api/sessions/missing/messages", headers=HEADERS, json={"text": "x"})
    assert r.status_code == 404
