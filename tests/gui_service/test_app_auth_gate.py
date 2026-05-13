"""The FastAPI app must reject requests without the bearer token."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
def config():
    return ServiceConfig(
        host="127.0.0.1",
        port=0,
        token="test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_root=".",
    )


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_rejects_missing_bearer(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_rejects_wrong_bearer(client):
    resp = await client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_rejects_wrong_scheme(client, config):
    resp = await client.get("/api/health", headers={"Authorization": f"Basic {config.token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_accepts_valid_bearer(client, config):
    resp = await client.get(
        "/api/health",
        headers={"Authorization": f"Bearer {config.token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_401_includes_www_authenticate_header(client):
    """RFC 6750: 401 responses must include WWW-Authenticate: Bearer."""
    resp = await client.get("/api/health")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
