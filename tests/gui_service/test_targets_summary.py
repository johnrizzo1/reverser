"""GET /api/targets/{name}/summary rolls up per-target sessions + KB stats."""
import json
import pytest
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    (tmp_path / "targets" / "10.10.10.5").mkdir(parents=True)
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_summary_for_empty_target(client, tmp_path):
    """A target dir with no sessions and no KB returns all-zero counts."""
    r = await client.get("/api/targets/10.10.10.5/summary", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "10.10.10.5"
    assert body["sessions"]["total"] == 0
    assert body["sessions"]["by_state"] == {
        "active": 0, "stopped": 0, "completed": 0, "abandoned": 0,
    }
    assert body["spend"]["total_usd"] == 0.0
    assert body["profiles_used"] == []
    assert body["first_activity"] is None
    assert body["last_activity"] is None
    for key in ("hosts", "services", "credentials",
                "findings", "hypotheses", "artifacts", "notes"):
        assert body["kb_counts"][key] == 0


@pytest.mark.asyncio
async def test_summary_404_when_target_dir_missing(client):
    r = await client.get("/api/targets/no-such-target/summary", headers=HEADERS)
    assert r.status_code == 404


def _write_snapshot(tmp_path, target, session_id, state, profile, cost, started_at, stopped_at=None):
    """Write a SessionSnapshot JSON the way reverser.sessions.save does."""
    target_dir = tmp_path / "targets" / target / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "version": 1,
        "session_id": session_id,
        "target": target,
        "log_path": str(tmp_path / "logs" / f"{target}.jsonl"),
        "config": {
            "profile": profile,
            "backend": "claude",
            "model": None,
            "api_base": None,
            "budget": 5.0,
            "max_turns": 50,
        },
        "stats": {
            "turns": 3,
            "total_cost": cost,
        },
        "state": state,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "pid": None,
        "conversation": [],
    }
    (target_dir / f"{session_id}.json").write_text(json.dumps(snap))


@pytest.mark.asyncio
async def test_summary_aggregates_session_states_and_cost(client, tmp_path):
    """Three sessions for a target — counts roll up by state, costs sum."""
    _write_snapshot(tmp_path, "10.10.10.5", "2026-05-09T18-22-00",
                    "completed", "general", 0.50,
                    "2026-05-09T18:22:00Z", "2026-05-09T19:00:00Z")
    _write_snapshot(tmp_path, "10.10.10.5", "2026-05-11T09-15-00",
                    "stopped", "manager", 0.30,
                    "2026-05-11T09:15:00Z", "2026-05-11T10:00:00Z")
    _write_snapshot(tmp_path, "10.10.10.5", "2026-05-13T11-04-00",
                    "active", "manager", 0.62,
                    "2026-05-13T11:04:00Z", None)

    r = await client.get("/api/targets/10.10.10.5/summary", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sessions"]["total"] == 3
    assert body["sessions"]["by_state"] == {
        "active": 1, "stopped": 1, "completed": 1, "abandoned": 0,
    }
    assert abs(body["spend"]["total_usd"] - (0.50 + 0.30 + 0.62)) < 1e-6
    # Two distinct profiles; "manager" used twice so listed first.
    assert body["profiles_used"] == ["manager", "general"]
    assert body["first_activity"] == "2026-05-09T18:22:00Z"
    # Latest activity = most recent started_at (active) or stopped_at.
    assert body["last_activity"] == "2026-05-13T11:04:00Z"
