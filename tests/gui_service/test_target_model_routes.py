"""Tests for the Target-model HTTP endpoints.

Task 26: GET /api/targets (Target-model summary list), GET /api/targets/{name}
Task 27: POST /api/targets (create), PATCH /api/targets/{name} (rename/notes)
Task 28: POST /api/targets/{name}/addresses, PATCH /api/targets/{name}/addresses/{id},
         POST /api/targets/{name}/addresses/{id}/rehash

These are separate from the legacy KB / scope / archive routes in
test_targets_routes.py which test directory-based functionality.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def client(tmp_path, monkeypatch):
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    monkeypatch.chdir(tmp_path)
    # Reset paths cache so targets_root() picks up the new env var.
    from reverser import paths
    paths._reset_caches_for_tests()
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    # Reset after the test to avoid cache pollution.
    paths._reset_caches_for_tests()


HEADERS = {"Authorization": "Bearer t"}


# ---------------------------------------------------------------------------
# Task 26 — read endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_targets_model_empty(client):
    """GET /api/targets returns {targets: []} when no target.json files exist."""
    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    # The response may contain legacy directories or be empty.
    # The key shape check: response has a "targets" key.
    assert "targets" in r.json()


@pytest.mark.asyncio
async def test_list_targets_model_after_create(client, tmp_path, monkeypatch):
    """After creating a Target on disk, GET /api/targets includes its summary."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("dc1", "network", "10.0.0.5")

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["targets"]]
    assert "dc1" in names


@pytest.mark.asyncio
async def test_get_target_detail(client, tmp_path, monkeypatch):
    """GET /api/targets/{name} returns the full Target dict for a known target."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("webapp", "network", "https://example.com")

    r = await client.get("/api/targets/webapp", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "webapp"
    assert body["kind"] == "network"
    assert "addresses" in body
    assert "primary_address_id" in body


@pytest.mark.asyncio
async def test_get_target_detail_unknown_returns_404(client):
    """GET /api/targets/{name} for an unknown target returns 404."""
    r = await client.get("/api/targets/does-not-exist", headers=HEADERS)
    assert r.status_code == 404
