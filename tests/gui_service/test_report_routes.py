"""GET + POST /api/targets/{name}/report renders/writes the engagement
report using the same _render_report function as kb_export_report."""
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
async def test_get_report_renders_markdown(client):
    r = await client.get("/api/targets/10.10.10.5/report", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "10.10.10.5"
    assert "Penetration Test Report" in body["markdown"]
    assert body["bytes"] == len(body["markdown"].encode())
    assert "generated_at" in body


@pytest.mark.asyncio
async def test_post_report_writes_to_disk(client, tmp_path):
    r = await client.post("/api/targets/10.10.10.5/report", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    report_path = Path(body["path"])
    assert report_path.is_file()
    content = report_path.read_text()
    assert "Penetration Test Report" in content
    assert body["bytes"] == len(content.encode())


@pytest.mark.asyncio
async def test_report_404_for_unknown_target(client):
    r = await client.get("/api/targets/no-such/report", headers=HEADERS)
    assert r.status_code == 404
    r2 = await client.post("/api/targets/no-such/report", headers=HEADERS)
    assert r2.status_code == 404
