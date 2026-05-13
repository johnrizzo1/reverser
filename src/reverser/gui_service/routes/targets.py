"""GET /api/targets + GET /api/targets/{name}/kb (read-only KB view).

The KB is the SQLite state at targets/<name>/state.db. We use the existing
reverser.kb.for_target helper to load it. Empty KB tables come back as
empty lists.

Note: KB method names differ from the plan spec — actual names are:
  get_hosts, get_services, get_credentials, get_findings,
  get_artifacts, get_notes, list_hypotheses
"""
import os
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...kb import for_target
from ...sessions import list_all as list_all_snapshots

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


def _summarize_target(target: str) -> dict:
    """Roll up snapshots and KB counts for one target. Caller must have
    already verified that the target directory exists."""
    snapshots = [s for s in list_all_snapshots() if s.target == target]

    by_state = {"active": 0, "stopped": 0, "completed": 0, "abandoned": 0}
    profile_counts: Counter[str] = Counter()
    total_cost = 0.0
    started_values = []
    last_values = []

    for s in snapshots:
        # Defensive: an unknown state slot would silently disappear without this.
        if s.state in by_state:
            by_state[s.state] += 1
        profile_counts[s.config.profile] += 1
        total_cost += float(s.stats.total_cost or 0.0)
        if s.started_at:
            started_values.append(s.started_at)
        # last_activity: stopped_at if present, else started_at (for active sessions).
        last_values.append(s.stopped_at or s.started_at)

    first_activity = min(started_values) if started_values else None
    last_activity = max(v for v in last_values if v) if any(last_values) else None
    profiles_used = [p for p, _ in profile_counts.most_common()]

    # KB counts via the existing list helpers. Use len() since the store
    # doesn't expose dedicated count_* methods.
    try:
        kb = for_target(target)
    except Exception:
        kb = None

    def _count(method_name: str) -> int:
        if kb is None:
            return 0
        fn = getattr(kb, method_name, None)
        if fn is None:
            return 0
        try:
            return len(list(fn()))
        except Exception:
            return 0

    kb_counts = {
        "hosts":       _count("get_hosts"),
        "services":    _count("get_services"),
        "credentials": _count("get_credentials"),
        "findings":    _count("get_findings"),
        "hypotheses":  _count("list_hypotheses"),
        "artifacts":   _count("get_artifacts"),
        "notes":       _count("get_notes"),
    }

    return {
        "target": target,
        "sessions": {"total": len(snapshots), "by_state": by_state},
        "spend": {"total_usd": round(total_cost, 6)},
        "profiles_used": profiles_used,
        "first_activity": first_activity,
        "last_activity": last_activity,
        "kb_counts": kb_counts,
    }


@router.get("/api/targets/{target}/summary")
def read_summary(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    return _summarize_target(target)
