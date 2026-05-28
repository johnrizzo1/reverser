"""GET /api/profiles enumerates the profile registry."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client():
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=".")
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_profiles_list_shape(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    body = resp.json()
    assert "profiles" in body
    assert isinstance(body["profiles"], list)
    assert len(body["profiles"]) >= 15  # 15 currently registered

    # Each item has the documented shape
    one = body["profiles"][0]
    for key in ("key", "name", "description", "domain", "skills", "tools_allowlist"):
        assert key in one


@pytest.mark.asyncio
async def test_profiles_includes_known_keys(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    keys = {p["key"] for p in resp.json()["profiles"]}
    # Spot-check a few profiles we know are registered
    for k in ("general", "ctf", "manager", "webpentest", "ad"):
        assert k in keys, f"missing expected profile: {k}"


@pytest.mark.asyncio
async def test_profile_skill_shape(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    manager = next(p for p in resp.json()["profiles"] if p["key"] == "manager")
    assert isinstance(manager["skills"], list)
    if manager["skills"]:
        s = manager["skills"][0]
        for key in ("name", "key", "description"):
            assert key in s
        # We do NOT expose the skill prompt over the API (it is internal model context).
        assert "prompt" not in s


@pytest.mark.asyncio
async def test_profiles_expose_domain_for_launcher_guidance(client):
    resp = await client.get("/api/profiles", headers={"Authorization": "Bearer t"})
    profiles = {p["key"]: p for p in resp.json()["profiles"]}
    assert profiles["general"]["domain"] == "binary"
    assert profiles["webpentest"]["domain"] == "web"
    assert profiles["manager"]["domain"] == "network"
