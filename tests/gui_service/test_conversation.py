"""GET /api/sessions/conversation/{id}?target=t serves a snapshot's
conversation history for the frontend's read-only chat replay."""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


def _write_snapshot_with_conversation(tmp_path, target, session_id, conversation):
    """Write a SessionSnapshot JSON with the given conversation history."""
    target_dir = tmp_path / "targets" / target / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "version": 1,
        "session_id": session_id,
        "target": target,
        "log_path": str(tmp_path / "logs" / f"{target}.jsonl"),
        "config": {
            "profile": "webpentest", "backend": "claude", "model": None,
            "api_base": None, "budget": 5.0, "max_turns": 50,
        },
        "stats": {"turns": len(conversation), "total_cost": 0.30},
        "state": "stopped",
        "started_at": "2026-05-12T22:54:46Z",
        "stopped_at": "2026-05-12T23:14:00Z",
        "pid": None,
        "conversation": conversation,
    }
    (target_dir / f"{session_id}.json").write_text(json.dumps(snap))


@pytest.mark.asyncio
async def test_conversation_returns_history(client, tmp_path):
    convo = [
        {"user": "look at the login form", "agent": "Found 3 fields…",
         "turn": 1, "timestamp": "2026-05-12T22:55:14Z", "cost": 0.12},
        {"user": "try SQLi", "agent": "sqlmap negative…",
         "turn": 2, "timestamp": "2026-05-12T23:00:00Z", "cost": 0.18},
    ]
    _write_snapshot_with_conversation(
        tmp_path, "app.example.com", "2026-05-12T22-54-46", convo,
    )

    r = await client.get(
        "/api/sessions/conversation/2026-05-12T22-54-46?target=app.example.com",
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "2026-05-12T22-54-46"
    assert body["target"] == "app.example.com"
    assert body["profile"] == "webpentest"
    assert body["state"] == "stopped"
    assert len(body["conversation"]) == 2
    assert body["conversation"][0]["user"] == "look at the login form"
    assert body["conversation"][1]["turn"] == 2


@pytest.mark.asyncio
async def test_conversation_404_for_unknown_session(client):
    r = await client.get(
        "/api/sessions/conversation/missing?target=10.10.10.5",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_conversation_requires_target_query(client):
    r = await client.get(
        "/api/sessions/conversation/some-id",
        headers=HEADERS,
    )
    # FastAPI maps missing required query params to 422 (unprocessable entity).
    assert r.status_code == 422
