"""GET /api/backends — static metadata about each supported backend.

Live model-list discovery (calling /v1/models on local backends) is a
Phase 1 feature; this endpoint returns only static metadata.
"""
from fastapi import APIRouter

router = APIRouter()


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
