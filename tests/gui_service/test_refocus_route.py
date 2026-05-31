import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig

HEADERS = {"Authorization": "Bearer t"}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_refocus_route_changes_primary(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.targets import create_target, load_target
    create_target(name="box", kind="network", initial_address="10.0.0.1")
    r = await client.post("/api/targets/box/refocus", headers=HEADERS, json={"new_ip": "10.0.0.2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["old_ip"] == "10.0.0.1" and body["new_ip"] == "10.0.0.2"
    assert load_target("box").primary_address.value == "10.0.0.2"


@pytest.mark.asyncio
async def test_refocus_route_unknown_target_404(client):
    r = await client.post("/api/targets/nope/refocus", headers=HEADERS, json={"new_ip": "10.0.0.2"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_refocus_route_out_of_scope_409(client, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.targets import create_target
    create_target(name="boxs", kind="network", initial_address="10.0.0.1")
    (tmp_path / "targets" / "boxs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "targets" / "boxs" / "scope.toml").write_text('[scope]\nin_scope_cidrs = ["10.0.0.0/29"]\n')
    r = await client.post("/api/targets/boxs/refocus", headers=HEADERS, json={"new_ip": "10.0.0.50"})
    assert r.status_code == 409
    # force overrides
    r2 = await client.post("/api/targets/boxs/refocus", headers=HEADERS,
                           json={"new_ip": "10.0.0.50", "force_scope": True})
    assert r2.status_code == 200
