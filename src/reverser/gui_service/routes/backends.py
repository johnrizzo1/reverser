"""Backend routes:

- GET /api/backends — static metadata about each supported backend.
- GET /api/backends/{backend}/models — live model list from local
  lmstudio/ollama via their OpenAI-compatible /v1/models endpoint.
"""
import httpx
from fastapi import APIRouter, HTTPException, Query

from reverser.backends import DEFAULT_API_BASES, resolve_api_base

router = APIRouter()


# Module-level so tests can monkeypatch with a MockTransport-backed client.
def _http_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(**kwargs)


_BACKENDS = [
    {
        "key": "claude",
        "name": "Claude (Anthropic)",
        "default_api_base": None,
        "requires_api_key": True,
        "requires_model": False,
    },
    {
        "key": "ollama",
        "name": "Ollama (local)",
        "default_api_base": "http://localhost:11434/v1",
        "requires_api_key": False,
        "requires_model": True,
    },
    {
        "key": "lmstudio",
        "name": "LM Studio (local)",
        "default_api_base": "http://localhost:1234/v1",
        "requires_api_key": False,
        "requires_model": True,
    },
    {
        "key": "local",
        "name": "OpenAI-compatible (custom)",
        "default_api_base": None,
        "requires_api_key": False,
        "requires_model": True,
    },
]


@router.get("/api/backends")
def get_backends() -> dict:
    return {"backends": list(_BACKENDS)}


@router.get("/api/backends/{backend}/models")
async def list_backend_models(
    backend: str,
    api_base: str | None = Query(default=None),
) -> dict:
    if backend not in DEFAULT_API_BASES:
        raise HTTPException(
            404,
            detail=f"model discovery not supported for backend '{backend}'",
        )
    base = resolve_api_base(backend, api_base).rstrip("/")
    url = f"{base}/models"
    try:
        async with _http_client(timeout=3.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            payload = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(
            502,
            detail={
                "error": "unreachable",
                "detail": f"could not fetch models from {base}: {e.__class__.__name__}",
            },
        )

    raw = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raw = []
    ids = sorted(
        {str(item["id"]) for item in raw if isinstance(item, dict) and "id" in item}
    )
    return {"models": [{"id": mid} for mid in ids]}
