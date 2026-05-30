"""GET /api/backends/{backend}/models — proxies /v1/models to local lmstudio/ollama."""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response, ConnectError

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from reverser.gui_service.routes import backends as backends_route


@pytest.fixture
async def client():
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=".")
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _install_mock_transport(monkeypatch, handler):
    """Replace the route's httpx.AsyncClient with one using a MockTransport."""
    def factory(**kwargs):
        return httpx.AsyncClient(transport=MockTransport(handler), **kwargs)
    monkeypatch.setattr(backends_route, "_http_client", factory)


@pytest.mark.asyncio
async def test_models_happy_path_sorted(client, monkeypatch):
    seen: list[str] = []

    def handler(request: Request) -> Response:
        seen.append(str(request.url))
        return Response(200, json={"data": [
            {"id": "qwen3.5:35b"},
            {"id": "llama3.1:8b"},
            {"id": "phi3:mini"},
        ]})

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/lmstudio/models",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"models": [
        {"id": "llama3.1:8b"},
        {"id": "phi3:mini"},
        {"id": "qwen3.5:35b"},
    ]}
    # Default api_base used because query param omitted
    assert seen == ["http://localhost:1234/v1/models"]


@pytest.mark.asyncio
async def test_models_uses_custom_api_base(client, monkeypatch):
    seen: list[str] = []

    def handler(request: Request) -> Response:
        seen.append(str(request.url))
        return Response(200, json={"data": [{"id": "qwen3.5:35b"}]})

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/ollama/models?api_base=http://192.168.1.10:11434/v1",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 200
    assert seen == ["http://192.168.1.10:11434/v1/models"]


@pytest.mark.asyncio
async def test_models_lmstudio_uses_custom_api_base(client, monkeypatch):
    seen: list[str] = []

    def handler(request: Request) -> Response:
        seen.append(str(request.url))
        return Response(200, json={"data": [{"id": "remote-model"}]})

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/lmstudio/models?api_base=http://192.168.1.50:1234/v1",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 200
    assert seen == ["http://192.168.1.50:1234/v1/models"]


@pytest.mark.asyncio
async def test_models_lmstudio_blank_api_base_uses_default(client, monkeypatch):
    seen: list[str] = []

    def handler(request: Request) -> Response:
        seen.append(str(request.url))
        return Response(200, json={"data": []})

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/lmstudio/models?api_base=%20%20",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 200
    assert seen == ["http://localhost:1234/v1/models"]


@pytest.mark.asyncio
async def test_models_ollama_uses_default_when_omitted(client, monkeypatch):
    seen: list[str] = []

    def handler(request: Request) -> Response:
        seen.append(str(request.url))
        return Response(200, json={"data": []})

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/ollama/models",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"models": []}
    assert seen == ["http://localhost:11434/v1/models"]


@pytest.mark.asyncio
async def test_models_unreachable_returns_502(client, monkeypatch):
    def handler(request: Request) -> Response:
        raise ConnectError("connection refused")

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/lmstudio/models",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 502
    body = resp.json()
    # detail is FastAPI's wrapping of the HTTPException detail payload
    assert body["detail"]["error"] == "unreachable"
    assert "http://localhost:1234/v1" in body["detail"]["detail"]


@pytest.mark.asyncio
async def test_models_non_200_returns_502(client, monkeypatch):
    def handler(request: Request) -> Response:
        return Response(500, text="internal server error")

    _install_mock_transport(monkeypatch, handler)

    resp = await client.get(
        "/api/backends/lmstudio/models",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "unreachable"


@pytest.mark.asyncio
async def test_models_unsupported_backend_returns_404(client):
    resp = await client.get(
        "/api/backends/claude/models",
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_models_requires_auth(client):
    resp = await client.get("/api/backends/lmstudio/models")
    assert resp.status_code in (401, 403)
