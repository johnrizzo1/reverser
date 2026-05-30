"""GET screenshot list + image bytes for one finding."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from reverser.kb import FindingFact, for_target


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    findings_dir = tmp_path / "targets" / "10.10.10.5" / "findings" / "f-42"
    findings_dir.mkdir(parents=True)
    (findings_dir / "screenshot-1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"X" * 64)
    (findings_dir / "screenshot-2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"Y" * 128)
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_list_screenshots(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots",
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["finding_id"] == "f-42"
    indices = sorted(s["index"] for s in body["screenshots"])
    assert indices == [1, 2]
    by_index = {s["index"]: s for s in body["screenshots"]}
    assert by_index[1]["size_bytes"] == 72
    assert by_index[2]["size_bytes"] == 136
    assert "captured_at" in by_index[1]


@pytest.mark.asyncio
async def test_list_screenshots_404_for_unknown_finding(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/no-such/screenshots",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_screenshots_empty_for_known_finding_without_files(client):
    fid = for_target("10.10.10.5").record_finding(
        FindingFact(title="weak password", severity="medium", description="confirmed")
    )

    r = await client.get(
        f"/api/targets/10.10.10.5/findings/{fid}/screenshots",
        headers=HEADERS,
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"finding_id": str(fid), "screenshots": []}


@pytest.mark.asyncio
async def test_image_bytes_returns_png(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots/1",
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_image_bytes_404_for_missing_index(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots/99",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_image_bytes_rejects_path_traversal(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots/..%2F..%2Fetc%2Fpasswd",
        headers=HEADERS,
    )
    assert r.status_code in (404, 422)
