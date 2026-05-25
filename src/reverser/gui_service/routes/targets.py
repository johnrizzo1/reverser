"""GET /api/targets + GET /api/targets/{name}/kb (read-only KB view).

The KB is the SQLite state at targets/<name>/state.db. We use the existing
reverser.kb.for_target helper to load it. Empty KB tables come back as
empty lists.

Note: KB method names differ from the plan spec — actual names are:
  get_hosts, get_services, get_credentials, get_findings,
  get_artifacts, get_notes, list_hypotheses

Target-model endpoints (Tasks 26-28):
  GET  /api/targets                              — Target model summary list
  GET  /api/targets/{name}                       — Full Target dict
  POST /api/targets                              — Create a new Target
  PATCH /api/targets/{name}                      — Rename / update notes
  POST /api/targets/{name}/addresses             — Add an address
  PATCH /api/targets/{name}/addresses/{id}       — Set primary / retire
"""
import ipaddress
import logging
import os
import re
import shutil
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, model_validator

from ...kb import for_target
from ...sessions import is_session_alive
from ...sessions import list_all as list_all_snapshots
from ...sessions import target_key
from ...tools.kb import _render_report

router = APIRouter()


class ScopeBody(BaseModel):
    in_scope_cidrs: list[str]
    out_of_scope_ips: list[str]
    allowed_hours: str | None
    no_dos: bool
    no_account_lockout: bool


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


_TRASH_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-(.+)$")
_TRASH_RETENTION = timedelta(days=30)


def _trash_dir() -> Path:
    return _targets_root() / ".trash"


def _prune_trash(now: datetime | None = None) -> None:
    """Remove entries older than _TRASH_RETENTION. Silently ignores entries
    whose names don't start with an ISO timestamp."""
    trash = _trash_dir()
    if not trash.is_dir():
        return
    cutoff = (now or datetime.now(timezone.utc)) - _TRASH_RETENTION
    log = logging.getLogger(__name__)
    for entry in trash.iterdir():
        m = _TRASH_RE.match(entry.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if ts < cutoff:
            try:
                shutil.rmtree(entry)
            except OSError as e:
                log.warning("failed to prune trash entry %s: %s", entry, e)


def _has_active_session(target: str) -> bool:
    """True iff any snapshot for this target has state == 'active' AND
    its recorded pid is still a live process.

    Normalizes both the query target and stored target via target_key() so
    that absolute paths (as stored by AgentSession) match canonical names.

    The is_session_alive() filter prevents stale "active" snapshots from a
    crashed process from blocking archive/delete forever.
    """
    try:
        query_key = target_key(target)
    except ValueError:
        return False
    for s in list_all_snapshots():
        if s.state != "active":
            continue
        if not is_session_alive(s):
            continue
        try:
            if target_key(s.target) == query_key:
                return True
        except ValueError:
            continue
    return False


@router.get("/api/targets")
def list_targets() -> dict:
    _prune_trash()
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
            "archived": (child / ".archived").is_file(),
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


# ---------------------------------------------------------------------------
# Scope endpoints
# ---------------------------------------------------------------------------

def _scope_path(target: str):
    return _targets_root() / target / "scope.toml"


def _serialize_scope_toml(body: ScopeBody) -> str:
    """Render the [scope] section of scope.toml. Manual assembly — fields
    are fixed and small."""
    def _emit_list(name: str, vals: list[str]) -> str:
        if not vals:
            return f"{name} = []\n"
        joined = ", ".join(f'"{v}"' for v in vals)
        return f"{name} = [{joined}]\n"
    hours = "" if body.allowed_hours is None else f'allowed_hours = "{body.allowed_hours}"\n'
    return (
        "[scope]\n"
        + _emit_list("in_scope_cidrs", body.in_scope_cidrs)
        + _emit_list("out_of_scope_ips", body.out_of_scope_ips)
        + hours
        + f"no_dos = {'true' if body.no_dos else 'false'}\n"
        + f"no_account_lockout = {'true' if body.no_account_lockout else 'false'}\n"
    )


def _validate_scope(body: ScopeBody) -> dict[str, str]:
    """Return a per-field error map; empty dict means valid."""
    errors: dict[str, str] = {}
    for i, cidr in enumerate(body.in_scope_cidrs):
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            errors[f"in_scope_cidrs[{i}]"] = f"invalid CIDR: {cidr!r}"
    for i, ip in enumerate(body.out_of_scope_ips):
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            errors[f"out_of_scope_ips[{i}]"] = f"invalid IP: {ip!r}"
    return errors


@router.get("/api/targets/{target}/scope")
def get_scope(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    path = _scope_path(target)
    if not path.is_file():
        return {
            "exists": False,
            "in_scope_cidrs": [],
            "out_of_scope_ips": [],
            "allowed_hours": None,
            "no_dos": False,
            "no_account_lockout": False,
        }
    import tomllib
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise HTTPException(500, detail=f"scope.toml unreadable: {e}")
    section = data.get("scope", {})
    return {
        "exists": True,
        "in_scope_cidrs": list(section.get("in_scope_cidrs", [])),
        "out_of_scope_ips": list(section.get("out_of_scope_ips", [])),
        "allowed_hours": section.get("allowed_hours"),
        "no_dos": bool(section.get("no_dos", False)),
        "no_account_lockout": bool(section.get("no_account_lockout", False)),
    }


@router.put("/api/targets/{target}/scope", status_code=204)
def put_scope(target: str, body: ScopeBody):
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    errors = _validate_scope(body)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    path = _scope_path(target)
    path.write_text(_serialize_scope_toml(body))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Report endpoints
# ---------------------------------------------------------------------------

@router.get("/api/targets/{target}/report")
def get_report(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    kb = for_target(target)
    markdown = _render_report(kb)
    return {
        "target": target,
        "markdown": markdown,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bytes": len(markdown.encode()),
    }


@router.post("/api/targets/{target}/report")
def export_report(target: str) -> dict:
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    kb = for_target(target)
    markdown = _render_report(kb)
    out_path = _targets_root() / target / "report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown)
    return {
        "target": target,
        "path": str(out_path.resolve()),
        "bytes": len(markdown.encode()),
    }


# ---------------------------------------------------------------------------
# Screenshot endpoints
# ---------------------------------------------------------------------------

def _findings_dir(target: str, finding_id: str):
    return _targets_root() / target / "findings" / finding_id


@router.get("/api/targets/{target}/findings/{finding_id}/screenshots")
def list_screenshots(target: str, finding_id: str) -> dict:
    d = _findings_dir(target, finding_id)
    if not d.is_dir():
        raise HTTPException(404, detail=f"unknown finding: {finding_id!r}")
    entries = []
    for f in sorted(d.glob("screenshot-*.png")):
        stem = f.stem  # e.g. "screenshot-1"
        try:
            idx = int(stem.removeprefix("screenshot-"))
        except ValueError:
            continue
        stat = f.stat()
        entries.append({
            "index": idx,
            "size_bytes": stat.st_size,
            "captured_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
        })
    entries.sort(key=lambda e: e["index"])
    return {"finding_id": finding_id, "screenshots": entries}


@router.get("/api/targets/{target}/findings/{finding_id}/screenshots/{n}")
def get_screenshot(target: str, finding_id: str, n: str):
    if not n.isdigit():
        raise HTTPException(404, detail="invalid screenshot index")
    idx = int(n)
    d = _findings_dir(target, finding_id)
    path = d / f"screenshot-{idx}.png"
    if not path.is_file():
        raise HTTPException(404, detail=f"screenshot {idx} not found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Archive / unarchive / soft-delete endpoints
# ---------------------------------------------------------------------------

@router.post("/api/targets/{target}/archive", status_code=204)
def archive_target(target: str) -> Response:
    target_dir = _targets_root() / target
    if not target_dir.is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    if _has_active_session(target):
        raise HTTPException(409, detail="target has an active session; stop it first")
    marker = target_dir / ".archived"
    marker.write_text(
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    return Response(status_code=204)


@router.delete("/api/targets/{target}/archive", status_code=204)
def unarchive_target(target: str) -> Response:
    target_dir = _targets_root() / target
    if not target_dir.is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    marker = target_dir / ".archived"
    if marker.exists():
        marker.unlink()
    return Response(status_code=204)


@router.delete("/api/targets/{target}", status_code=204)
def delete_target(target: str) -> Response:
    target_dir = _targets_root() / target
    if not target_dir.is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    if _has_active_session(target):
        raise HTTPException(409, detail="target has an active session; stop it first")

    trash = _trash_dir()
    trash.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    dest = trash / f"{stamp}-{target}"
    # Avoid collision (two deletions in the same second)
    suffix = 0
    while dest.exists():
        suffix += 1
        dest = trash / f"{stamp}-{target}.{suffix}"
    shutil.move(str(target_dir), str(dest))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Target model CRUD endpoints (Tasks 26-28)
# ---------------------------------------------------------------------------

# Lazy import to avoid circular dependency at module load time.
def _targets_mod():
    from reverser import targets as _tmod
    return _tmod


# --- Helpers ---

def _target_summary(t) -> dict:
    """Return a concise summary dict for a Target object."""
    return {
        "name": t.name,
        "kind": t.kind,
        "primary_address": t.primary_address.value,
        "address_count": len(t.addresses),
        "updated_at": t.updated_at,
    }


def _target_detail(t) -> dict:
    """Return the full Target dict."""
    return t.to_dict()


# --- Task 26: Read endpoints ---

@router.get("/api/targets/{name}")
def get_target_detail(name: str) -> dict:
    """Return the full Target model dict for a known target.

    NOTE: FastAPI matches more-specific routes first (e.g. /api/targets/{target}/kb)
    before falling back to this bare-name route.
    """
    tmod = _targets_mod()
    try:
        t = tmod.load_target(name)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"unknown target: {name!r}")
    return _target_detail(t)
