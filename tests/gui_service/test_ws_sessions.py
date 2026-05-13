"""WebSocket subscribes to the EventBus and forwards JSON frames.

We use FastAPI's TestClient (sync) for WebSocket tests — httpx.AsyncClient
doesn't yet support WebSocket directly in a clean way.
"""
import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    return TestClient(create_app(config))


def test_ws_requires_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/sessions/anything"):
            pass


def test_ws_rejects_wrong_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/sessions/x?token=wrong"):
            pass


def test_ws_receives_published_frames(client, tmp_path):
    # Create an active session via the REST API first
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        r = client.post(
            "/api/sessions",
            headers={"Authorization": "Bearer t"},
            json={
                "target": str(tmp_path / "bin"), "profile": "general",
                "backend": "claude", "model": None, "api_base": None,
                "budget": 5.0, "max_turns": 50,
            },
        )
    assert r.status_code == 200
    sid = r.json()["id"]

    # Subscribe to the WS, then send a message, then drain frames.
    with client.websocket_connect(f"/ws/sessions/{sid}?token=t") as ws:
        with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
            client.post(
                f"/api/sessions/{sid}/messages",
                headers={"Authorization": "Bearer t"},
                json={"text": "hi"},
            )
        # Collect frames until we see the terminal "awaiting_input" status.
        # Starlette's WebSocketTestSession.receive_json() does not accept a
        # timeout keyword; we break on the sentinel frame instead.
        frames = []
        for _ in range(10):
            try:
                frames.append(ws.receive_json())
            except Exception:
                break
            if frames[-1].get("type") == "status" and frames[-1].get("phase") == "awaiting_input":
                break
    kinds = [f["type"] for f in frames]
    assert "text" in kinds or "status" in kinds
