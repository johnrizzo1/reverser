"""PATCH /api/sessions/{id}/config — edit a stopped engagement's config."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(
        host="127.0.0.1", port=0, token="t", project_root=str(tmp_path),
    )


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


async def _create_and_stop(client, tmp_path) -> tuple[str, str]:
    """Helper: create an engagement then stop it; returns (session_id, target)."""
    target = str(tmp_path / "bin")
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": target, "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        await client.post(f"/api/sessions/{sid}/stop", headers=HEADERS)
    return sid, target


@pytest.mark.asyncio
async def test_patch_config_updates_stopped_session(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"model": "qwen3.5:35b", "api_base": "http://localhost:11434/v1",
              "budget": 7.5, "max_turns": 75},
    )
    assert r.status_code == 204, r.text

    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["model"] == "qwen3.5:35b"
    assert row["api_base"] == "http://localhost:11434/v1"
    assert row["budget"] == 7.5
    assert row["max_turns"] == 75


@pytest.mark.asyncio
async def test_patch_config_partial_leaves_unsent_fields_unchanged(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": 9.0},
    )
    assert r.status_code == 204

    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["budget"] == 9.0
    assert row["max_turns"] == 50  # unchanged
    assert row["backend"] == "claude"  # unchanged


@pytest.mark.asyncio
async def test_patch_config_404_for_unknown_session(client, tmp_path):
    target = str(tmp_path / "bin")
    r = await client.patch(
        f"/api/sessions/nope/config?target={target}",
        headers=HEADERS,
        json={"budget": 1.0},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_config_409_for_active_session(client, tmp_path):
    target = str(tmp_path / "bin")
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": target, "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]

        r = await client.patch(
            f"/api/sessions/{sid}/config?target={target}",
            headers=HEADERS,
            json={"budget": 9.0},
        )
    assert r.status_code == 409
    assert "stop it first" in r.json()["detail"]


@pytest.mark.asyncio
async def test_patch_config_409_for_completed_session(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    # Transition stopped → completed via /done (works on stale-active too).
    done = await client.post(f"/api/sessions/{sid}/done", headers=HEADERS)
    assert done.status_code == 204

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": 9.0},
    )
    assert r.status_code == 409
    assert "completed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_patch_config_400_unknown_profile(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"profile": "nonexistent"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_400_unknown_backend(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"backend": "nonexistent"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_400_invalid_budget_or_max_turns(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    r1 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": -1.0},
    )
    assert r1.status_code == 400
    r2 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"max_turns": 0},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_explicit_null_clears_optional_field(client, tmp_path):
    """Sending {"model": null} clears a previously-set model. This must be
    distinguishable from {} (don't touch model) — the endpoint relies on
    Pydantic's `exclude_unset` to tell the two cases apart."""
    sid, target = await _create_and_stop(client, tmp_path)

    # Step 1: set model to a value.
    r1 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"model": "qwen3.5:35b"},
    )
    assert r1.status_code == 204
    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["model"] == "qwen3.5:35b"

    # Step 2: clear it with explicit null.
    r2 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"model": None},
    )
    assert r2.status_code == 204
    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["model"] is None


@pytest.mark.asyncio
async def test_patch_config_400_when_required_field_is_null(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    for field in ("profile", "backend", "budget", "max_turns"):
        r = await client.patch(
            f"/api/sessions/{sid}/config?target={target}",
            headers=HEADERS,
            json={field: None},
        )
        assert r.status_code == 400, f"expected 400 for null {field}, got {r.status_code}"


@pytest.mark.asyncio
async def test_patch_config_syncs_in_memory_state_for_cached_active(
    client, tmp_path,
):
    """When a stopped session is still cached in SessionManager.active,
    list_sessions overrides budget/max_turns from in-memory state. PATCH
    must update both the on-disk snapshot AND the cached in-memory state so
    a subsequent GET reflects the new values without a service restart.
    """
    sid, target = await _create_and_stop(client, tmp_path)

    # Two list_sessions calls bracket the PATCH so we can see the change.
    before = await client.get("/api/sessions", headers=HEADERS)
    before_row = next(
        r for r in before.json()["sessions"] if r["id"] == sid
    )
    assert before_row["budget"] == 5.0

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": 12.5, "max_turns": 99},
    )
    assert r.status_code == 204

    after = await client.get("/api/sessions", headers=HEADERS)
    after_row = next(
        r for r in after.json()["sessions"] if r["id"] == sid
    )
    assert after_row["budget"] == 12.5
    assert after_row["max_turns"] == 99
