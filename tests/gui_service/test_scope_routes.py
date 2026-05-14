"""GET + PUT /api/targets/{name}/scope edit and read scope.toml."""
import pytest
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
async def test_get_scope_returns_defaults_when_no_file(client):
    r = await client.get("/api/targets/10.10.10.5/scope", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] is False
    assert body["in_scope_cidrs"] == []
    assert body["out_of_scope_ips"] == []
    assert body["allowed_hours"] is None
    assert body["no_dos"] is False
    assert body["no_account_lockout"] is False


@pytest.mark.asyncio
async def test_put_scope_writes_file_and_returns_204(client, tmp_path):
    body = {
        "in_scope_cidrs": ["10.10.10.0/24"],
        "out_of_scope_ips": ["10.10.10.99"],
        "allowed_hours": "09:00-17:00 UTC",
        "no_dos": True,
        "no_account_lockout": True,
    }
    r = await client.put("/api/targets/10.10.10.5/scope", headers=HEADERS, json=body)
    assert r.status_code == 204, r.text
    r2 = await client.get("/api/targets/10.10.10.5/scope", headers=HEADERS)
    got = r2.json()
    assert got["exists"] is True
    assert got["in_scope_cidrs"] == ["10.10.10.0/24"]
    assert got["out_of_scope_ips"] == ["10.10.10.99"]
    assert got["allowed_hours"] == "09:00-17:00 UTC"
    assert got["no_dos"] is True
    assert got["no_account_lockout"] is True


@pytest.mark.asyncio
async def test_put_scope_rejects_invalid_cidr(client):
    body = {
        "in_scope_cidrs": ["10.10.10.0/24", "not-a-cidr"],
        "out_of_scope_ips": [],
        "allowed_hours": None,
        "no_dos": False,
        "no_account_lockout": False,
    }
    r = await client.put("/api/targets/10.10.10.5/scope", headers=HEADERS, json=body)
    assert r.status_code == 400, r.text
    errors = r.json().get("errors", {})
    assert "in_scope_cidrs[1]" in errors
    assert "not-a-cidr" in errors["in_scope_cidrs[1]"]


@pytest.mark.asyncio
async def test_put_scope_rejects_invalid_ip(client):
    body = {
        "in_scope_cidrs": [],
        "out_of_scope_ips": ["10.10.10.99", "999.999.999.999"],
        "allowed_hours": None,
        "no_dos": False,
        "no_account_lockout": False,
    }
    r = await client.put("/api/targets/10.10.10.5/scope", headers=HEADERS, json=body)
    assert r.status_code == 400, r.text
    errors = r.json().get("errors", {})
    assert "out_of_scope_ips[1]" in errors


@pytest.mark.asyncio
async def test_put_scope_with_all_empty_still_writes_file(client, tmp_path):
    body = {
        "in_scope_cidrs": [],
        "out_of_scope_ips": [],
        "allowed_hours": None,
        "no_dos": False,
        "no_account_lockout": False,
    }
    r = await client.put("/api/targets/10.10.10.5/scope", headers=HEADERS, json=body)
    assert r.status_code == 204, r.text
    r2 = await client.get("/api/targets/10.10.10.5/scope", headers=HEADERS)
    assert r2.json()["exists"] is True


@pytest.mark.asyncio
async def test_scope_404_for_unknown_target(client):
    r = await client.get("/api/targets/no-such/scope", headers=HEADERS)
    assert r.status_code == 404
    r2 = await client.put("/api/targets/no-such/scope", headers=HEADERS, json={
        "in_scope_cidrs": [], "out_of_scope_ips": [], "allowed_hours": None,
        "no_dos": False, "no_account_lockout": False,
    })
    assert r2.status_code == 404
