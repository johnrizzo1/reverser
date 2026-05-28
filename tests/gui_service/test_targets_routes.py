"""GET /api/targets and /api/targets/{t}/kb."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    # The autouse `_isolate_targets_dir` fixture in conftest.py already
    # points REVERSER_TARGETS_DIR at a fresh tmp dir. Override it here so
    # this test owns the directory layout and can pre-populate fixtures.
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(targets_dir))
    monkeypatch.chdir(tmp_path)
    (targets_dir / "10.10.10.5").mkdir(parents=True)
    (targets_dir / "example.com").mkdir(parents=True)
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


@pytest.mark.asyncio
async def test_read_kb_findings_include_ids(client):
    from reverser.kb import FindingFact, for_target

    kb = for_target("10.10.10.5")
    fid = kb.record_finding(FindingFact(
        title="Anonymous SMB share access",
        severity="medium",
        description="IPC$ allows anonymous enumeration.",
    ))

    r = await client.get("/api/targets/10.10.10.5/kb", headers=HEADERS)
    assert r.status_code == 200
    findings = r.json()["findings"]
    assert len(findings) == 1
    assert findings[0]["id"] == fid
