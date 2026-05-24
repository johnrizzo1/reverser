"""GET /api/health returns a snapshot of backend tool availability."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
def config():
    return ServiceConfig(
        host="127.0.0.1", port=0,
        token="t", project_root=".",
    )


@pytest.fixture
async def client(config):
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_shape(client):
    resp = await client.get("/api/health", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    body = resp.json()
    # Top-level keys
    assert body["ok"] is True
    assert "version" in body
    assert "checks" in body
    # Expected checks (each is {ok: bool, detail: str | null})
    checks = body["checks"]
    for key in (
        "python", "devenv_shell", "playwright_chromium", "msf_rpcd", "neo4j",
        "nmap", "ffuf", "gobuster", "nuclei", "testssl",
    ):
        assert key in checks
        assert "ok" in checks[key]
        assert "detail" in checks[key]
