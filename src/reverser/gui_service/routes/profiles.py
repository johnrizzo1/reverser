"""GET /api/profiles — the profile registry, serialized for the UI.

We deliberately omit the skill `prompt` field. The prompt is internal
model context; the UI only needs name/key/description to render the skill
picker.
"""
from fastapi import APIRouter

from ...profiles import list_profiles

router = APIRouter()


def _serialize_skill(s) -> dict:
    return {"name": s.name, "key": s.key, "description": s.description}


def _serialize_profile(p) -> dict:
    return {
        "key": p.key,
        "name": p.name,
        "description": p.description,
        "skills": [_serialize_skill(s) for s in p.skills],
        "tools_allowlist": p.tools_allowlist,  # None means "all tools"
    }


@router.get("/api/profiles")
def get_profiles() -> dict:
    return {"profiles": [_serialize_profile(p) for p in list_profiles()]}
