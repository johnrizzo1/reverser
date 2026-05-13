"""GET /api/targets + GET /api/targets/{name}/kb (read-only KB view).

The KB is the SQLite state at targets/<name>/state.db. We use the existing
reverser.kb.for_target helper to load it. Empty KB tables come back as
empty lists.

Note: KB method names differ from the plan spec — actual names are:
  get_hosts, get_services, get_credentials, get_findings,
  get_artifacts, get_notes, list_hypotheses
"""
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...kb import for_target

router = APIRouter()


def _as_jsonable(row):
    if is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
    return row


def _targets_root() -> Path:
    return Path(os.environ.get("REVERSER_TARGETS_DIR", "targets"))


@router.get("/api/targets")
def list_targets() -> dict:
    root = _targets_root()
    if not root.is_dir():
        return {"targets": []}
    targets = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        # Skip hidden / non-canonical directories ("." prefix, etc.)
        if child.name.startswith("."):
            continue
        targets.append({
            "name": child.name,
            "has_kb": (child / "state.db").is_file(),
            "has_scope": (child / "scope.toml").is_file(),
        })
    return {"targets": targets}


@router.get("/api/targets/{target}/kb")
def read_kb(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")

    try:
        kb = for_target(target)
    except Exception:
        # KB init failed (e.g. permission error); return empty state
        return {
            "hosts": [], "services": [], "credentials": [], "findings": [],
            "hypotheses": [], "artifacts": [], "notes": [],
        }

    def _list(method_name: str) -> list:
        fn = getattr(kb, method_name, None)
        if fn is None:
            return []
        try:
            return [_as_jsonable(r) for r in fn()]
        except Exception:
            return []

    return {
        "hosts": _list("get_hosts"),
        "services": _list("get_services"),
        "credentials": _list("get_credentials"),
        "findings": _list("get_findings"),
        "hypotheses": _list("list_hypotheses"),
        "artifacts": _list("get_artifacts"),
        "notes": _list("get_notes"),
    }
