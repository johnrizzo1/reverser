"""Tests for target archive/unarchive/soft-delete + trash prune."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    (tmp_path / "targets" / "10.10.10.5").mkdir(parents=True)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_archive_target_writes_marker(client, tmp_path):
    r = await client.post("/api/targets/10.10.10.5/archive", headers=HEADERS)
    assert r.status_code == 204
    assert (tmp_path / "targets" / "10.10.10.5" / ".archived").is_file()

    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    row = next(t for t in rows if t["name"] == "10.10.10.5")
    assert row["archived"] is True


@pytest.mark.asyncio
async def test_unarchive_target_removes_marker(client, tmp_path):
    await client.post("/api/targets/10.10.10.5/archive", headers=HEADERS)
    r = await client.delete("/api/targets/10.10.10.5/archive", headers=HEADERS)
    assert r.status_code == 204
    assert not (tmp_path / "targets" / "10.10.10.5" / ".archived").exists()

    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    row = next(t for t in rows if t["name"] == "10.10.10.5")
    assert row["archived"] is False


@pytest.mark.asyncio
async def test_soft_delete_target_moves_to_trash(client, tmp_path):
    r = await client.delete("/api/targets/10.10.10.5", headers=HEADERS)
    assert r.status_code == 204
    assert not (tmp_path / "targets" / "10.10.10.5").exists()

    trash = tmp_path / "targets" / ".trash"
    assert trash.is_dir()
    entries = list(trash.iterdir())
    assert len(entries) == 1
    # Filename: <YYYY-MM-DDTHH-MM-SS>-10.10.10.5
    name = entries[0].name
    assert name.endswith("-10.10.10.5"), f"unexpected trash entry name: {name}"

    # And subsequent GET /api/targets does not list the deleted target
    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    names = {t["name"] for t in rows}
    assert "10.10.10.5" not in names


@pytest.mark.asyncio
async def test_archive_target_with_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "targets" / "10.10.10.5"),
            "profile": "general", "backend": "claude",
            "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })

    r = await client.post("/api/targets/10.10.10.5/archive", headers=HEADERS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_soft_delete_target_with_active_session_returns_409(client, tmp_path):
    with patch("reverser.agent_session.create_backend",
               return_value=FakeBackend()):
        await client.post("/api/sessions", headers=HEADERS, json={
            "target": str(tmp_path / "targets" / "10.10.10.5"),
            "profile": "general", "backend": "claude",
            "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })

    r = await client.delete("/api/targets/10.10.10.5", headers=HEADERS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_archive_missing_target_returns_404(client):
    r = await client.post("/api/targets/does-not-exist/archive", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trash_prune_sweeps_old_entries(client, tmp_path):
    """Pre-seed .trash/ with a 31-day-old entry. Next GET /api/targets removes it."""
    trash = tmp_path / "targets" / ".trash"
    trash.mkdir(parents=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%dT%H-%M-%S")
    old_entry = trash / f"{old_ts}-someoldtarget"
    old_entry.mkdir()
    (old_entry / "marker.txt").write_text("payload")

    # Also seed a fresh entry that should survive
    fresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    fresh_entry = trash / f"{fresh_ts}-fresh"
    fresh_entry.mkdir()

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200

    assert not old_entry.exists(), "31-day-old entry should have been pruned"
    assert fresh_entry.exists(), "fresh entry should remain"


@pytest.mark.asyncio
async def test_trash_prune_ignores_unparseable_names(client, tmp_path):
    """Entries that don't start with an ISO timestamp are left alone."""
    trash = tmp_path / "targets" / ".trash"
    trash.mkdir(parents=True)
    weird = trash / "not-a-timestamp-anything"
    weird.mkdir()

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    assert weird.exists()


@pytest.mark.asyncio
async def test_list_targets_includes_archived_field(client):
    rows = (await client.get("/api/targets", headers=HEADERS)).json()["targets"]
    assert rows, "expected the seeded target"
    for t in rows:
        assert "archived" in t, f"row missing 'archived': {t}"
