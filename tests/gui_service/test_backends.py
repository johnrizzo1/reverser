"""GET /api/backends returns metadata for each supported backend."""
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
async def test_backends_shape(client):
    resp = await client.get("/api/backends", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    body = resp.json()
    assert "backends" in body
    backends = {b["key"]: b for b in body["backends"]}
    # All four known backends present
    for key in ("claude", "ollama", "lmstudio", "local"):
        assert key in backends
        b = backends[key]
        for field in ("key", "name", "default_api_base", "requires_api_key", "requires_model"):
            assert field in b


@pytest.mark.asyncio
async def test_claude_requires_api_key(client):
    resp = await client.get("/api/backends", headers={"Authorization": "Bearer t"})
    claude = next(b for b in resp.json()["backends"] if b["key"] == "claude")
    assert claude["requires_api_key"] is True
    assert claude["requires_model"] is False


@pytest.mark.asyncio
async def test_local_backends_require_model_not_key(client):
    resp = await client.get("/api/backends", headers={"Authorization": "Bearer t"})
    for k in ("ollama", "lmstudio", "local"):
        b = next(x for x in resp.json()["backends"] if x["key"] == k)
        assert b["requires_api_key"] is False
        assert b["requires_model"] is True
