"""GET /api/targets and /api/targets/{t}/kb."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Populate a fake targets/<t>/ directory so /api/targets has something
    (tmp_path / "targets" / "10.10.10.5").mkdir(parents=True)
    (tmp_path / "targets" / "example.com").mkdir(parents=True)
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_list_targets(client):
    r = await client.get("/api/targets", headers=HEADERS)
    assert r.status_code == 200
    targets = {t["name"] for t in r.json()["targets"]}
    assert "10.10.10.5" in targets
    assert "example.com" in targets


@pytest.mark.asyncio
async def test_read_kb_returns_keyed_lists(client):
    r = await client.get("/api/targets/10.10.10.5/kb", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    for key in ("hosts", "services", "credentials", "findings",
                "hypotheses", "artifacts", "notes"):
        assert key in body, f"missing key {key}"
        assert isinstance(body[key], list)
