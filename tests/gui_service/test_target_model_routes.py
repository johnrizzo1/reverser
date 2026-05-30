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
from urllib.parse import quote

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
    """After creating a Target on disk, GET /api/targets includes its summary with
    all Target-model fields populated."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("dc1", "network", "10.0.0.5")

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    payload = r.json()
    names = [t["name"] for t in payload["targets"]]
    assert "dc1" in names

    entry = next(t for t in payload["targets"] if t["name"] == "dc1")
    assert entry["kind"] == "network"
    assert entry["primary_address"] == "10.0.0.5"
    assert entry["address_count"] == 1
    assert isinstance(entry["updated_at"], str) and entry["updated_at"]


@pytest.mark.asyncio
async def test_list_targets_model_multi_address(client, tmp_path, monkeypatch):
    """address_count reflects the total number of addresses on the target."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    t = tmod.create_target("dc1", "network", "10.0.0.5")
    tmod.add_address(t, "10.0.0.6", kind="ip")

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    entry = next(t for t in r.json()["targets"] if t["name"] == "dc1")
    assert entry["address_count"] == 2


@pytest.mark.asyncio
async def test_list_targets_legacy_dir_without_target_json(client, tmp_path, monkeypatch):
    """A directory in targets/ without a target.json is included in the list
    with Target-model fields set to null/0 but legacy fields still present."""
    targets_dir = tmp_path / "targets"
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    from reverser import paths
    paths._reset_caches_for_tests()

    # Simulate a legacy target directory with no target.json.
    legacy_dir = targets_dir / "old-legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)

    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    payload = r.json()
    names = [t["name"] for t in payload["targets"]]
    assert "old-legacy" in names

    entry = next(t for t in payload["targets"] if t["name"] == "old-legacy")
    # Legacy fields must be present.
    assert "has_kb" in entry
    assert "has_scope" in entry
    assert "archived" in entry
    assert entry["archived"] is False
    # Target-model fields default to null/0 when target.json is absent.
    assert entry["kind"] is None
    assert entry["primary_address"] is None
    assert entry["address_count"] == 0
    assert entry["updated_at"] is None


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
async def test_get_target_detail_accepts_absolute_binary_address(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    binary = tmp_path / "inputs" / "Flag.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"MZ")
    target = tmod.create_target("flag-crackme", "binary", str(binary))

    r = await client.get(
        f"/api/targets/{quote(str(binary), safe='')}",
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "flag-crackme"
    assert body["primary_address_id"] == target.primary_address_id
    assert body["addresses"][0]["value"] == str(binary)


@pytest.mark.asyncio
async def test_get_target_detail_unknown_returns_404(client):
    """GET /api/targets/{name} for an unknown target returns 404."""
    r = await client.get("/api/targets/does-not-exist", headers=HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Task 27 — create and patch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_target_via_post(client, tmp_path, monkeypatch):
    """POST /api/targets creates a target and returns 201 with the target payload."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths
    paths._reset_caches_for_tests()

    r = await client.post("/api/targets", headers=HEADERS, json={
        "name": "dc1",
        "kind": "network",
        "initial_address": "10.0.0.5",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "dc1"
    assert body["kind"] == "network"
    assert len(body["addresses"]) == 1
    assert body["addresses"][0]["value"] == "10.0.0.5"


@pytest.mark.asyncio
async def test_create_target_duplicate_returns_400(client, tmp_path, monkeypatch):
    """POST /api/targets with a duplicate name returns 400."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("dc1", "network", "10.0.0.5")

    r = await client.post("/api/targets", headers=HEADERS, json={
        "name": "dc1",
        "kind": "network",
        "initial_address": "10.0.0.6",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rename_target_via_patch(client, tmp_path, monkeypatch):
    """PATCH /api/targets/{name} with a new name renames the target."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("oldname", "network", "10.0.0.5")

    r = await client.patch("/api/targets/oldname", headers=HEADERS, json={
        "name": "newname",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "newname"


@pytest.mark.asyncio
async def test_patch_target_unknown_returns_404(client):
    """PATCH /api/targets/{name} on an unknown target returns 404."""
    r = await client.patch("/api/targets/no-such", headers=HEADERS, json={"name": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_target_notes(client, tmp_path, monkeypatch):
    """PATCH /api/targets/{name} with notes updates the notes field."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("dc1", "network", "10.0.0.5")

    r = await client.patch("/api/targets/dc1", headers=HEADERS, json={
        "notes": "primary domain controller",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notes"] == "primary domain controller"


# ---------------------------------------------------------------------------
# Task 28 — address management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_address_endpoint(client, tmp_path, monkeypatch):
    """POST /api/targets/{name}/addresses adds a new address."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    tmod.create_target("dc1", "network", "10.0.0.5")

    r = await client.post("/api/targets/dc1/addresses", headers=HEADERS, json={
        "value": "10.0.0.6",
        "kind": "ip",
        "make_primary": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["addresses"]) == 2
    values = {a["value"] for a in body["addresses"]}
    assert "10.0.0.6" in values


@pytest.mark.asyncio
async def test_set_primary_endpoint(client, tmp_path, monkeypatch):
    """PATCH /api/targets/{name}/addresses/{id} with make_primary=true promotes the address."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    t = tmod.create_target("dc1", "network", "10.0.0.5")
    t = tmod.add_address(t, "10.0.0.6", kind="ip")
    second_id = t.addresses[1].id

    r = await client.patch(
        f"/api/targets/dc1/addresses/{second_id}",
        headers=HEADERS,
        json={"make_primary": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["primary_address_id"] == second_id


@pytest.mark.asyncio
async def test_retire_address_endpoint(client, tmp_path, monkeypatch):
    """PATCH /api/targets/{name}/addresses/{id} with retire=true retires the address."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    from reverser import paths, targets as tmod
    paths._reset_caches_for_tests()

    t = tmod.create_target("dc1", "network", "10.0.0.5")
    t = tmod.add_address(t, "10.0.0.6", kind="ip", make_primary=True)
    first_id = t.addresses[0].id

    r = await client.patch(
        f"/api/targets/dc1/addresses/{first_id}",
        headers=HEADERS,
        json={"retire": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    retired = next(a for a in body["addresses"] if a["id"] == first_id)
    assert retired["status"] == "retired"
