# Phase 3b — Per-Target Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three per-target features to the desktop UI: a scope.toml form editor (modal from `TargetOverview`), a report preview tab (renders Markdown from `kb_export_report`), and a screenshot evidence lightbox (shared across `FindingsPane` and `KBTabbedView`).

**Architecture:** Five new read/write endpoints under `routes/targets.py` (scope GET/PUT, report GET/POST, screenshots list+image bytes). Server-side scope validation uses Python's `ipaddress` module. The report endpoint reuses the existing `_render_report` function from `tools/kb.py`. Frontend gains four new components plus one shared row component pulled out of two existing inline duplicates.

**Tech Stack:** Python 3.11+, FastAPI, existing `reverser.kb` modules (Scope, `_render_report`, `for_target`), React 18, TypeScript, TanStack Query, `react-markdown` + `remark-gfm` (new deps), Tailwind.

**Reference spec:** [`docs/superpowers/specs/2026-05-13-phase-3b-per-target-polish-design.md`](../specs/2026-05-13-phase-3b-per-target-polish-design.md).

---

## File map

```
Backend:
  src/reverser/gui_service/routes/targets.py        modify  (5 endpoints + helpers)
  tests/gui_service/test_scope_routes.py            create  (~6 tests)
  tests/gui_service/test_report_routes.py           create  (~3 tests)
  tests/gui_service/test_screenshot_routes.py       create  (~4 tests)

Frontend:
  desktop/package.json                              modify  (+ react-markdown, + remark-gfm)
  desktop/renderer/src/
    api/client.ts                                   modify  (+ Scope, ReportResponse,
                                                            ScreenshotsResponse types)
    api/queries.ts                                  modify  (+ useScope, useUpdateScope,
                                                            useReport, useExportReport,
                                                            useScreenshots)
    components/
      FindingRow.tsx                                create  (shared row)
    modals/
      ScopeEditorModal.tsx                          create
      ScreenshotLightboxModal.tsx                   create
    panes/
      ReportTab.tsx                                 create
      FindingsPane.tsx                              modify  (use shared FindingRow,
                                                            wire lightbox)
    components/KBTabbedView.tsx                     modify  (shared FindingRow + Report tab
                                                            + lightbox callback)
    pages/TargetOverview.tsx                        modify  ("Edit scope" button +
                                                            ScopeEditorModal mount)
  desktop/tests/e2e/phase3b.spec.ts                 create  (~4 Playwright tests)
```

---

## Task 1: Scope GET + PUT endpoints

Both endpoints live in the existing `routes/targets.py`. The Scope dataclass already exists in `src/reverser/kb/scope.py` (`in_scope_cidrs`, `out_of_scope_ips`, `allowed_hours`, `no_dos`, `no_account_lockout`). `load_scope(target) -> Optional[Scope]` reads `targets/<t>/scope.toml`. The TOML structure uses a `[scope]` section.

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Test: `tests/gui_service/test_scope_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_scope_routes.py`:

```python
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

    # File was written; round-trip read returns the same values.
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
    # File exists now; GET reports exists=true.
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
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_scope_routes.py -v`
Expected: FAIL — 404 (routes don't exist).

- [ ] **Step 3: Implement the endpoints**

Open `src/reverser/gui_service/routes/targets.py`. At the top, add imports:

```python
import ipaddress
from pydantic import BaseModel
```

(`tomllib` is already in stdlib for reading; we'll do manual TOML serialization for writing.)

Add a Pydantic body model near the top of the module (after existing imports):

```python
class ScopeBody(BaseModel):
    in_scope_cidrs: list[str]
    out_of_scope_ips: list[str]
    allowed_hours: str | None
    no_dos: bool
    no_account_lockout: bool
```

Append a helper function and the two route handlers (placement: after the existing `read_kb` handler):

```python
def _scope_path(target: str):
    return _targets_root() / target / "scope.toml"


def _serialize_scope_toml(body: ScopeBody) -> str:
    """Render the [scope] section of scope.toml. Manual assembly — the
    fields are fixed and small, no dep needed."""
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
        raise HTTPException(status_code=400, detail={"errors": errors})
    path = _scope_path(target)
    path.write_text(_serialize_scope_toml(body))
    from fastapi import Response
    return Response(status_code=204)
```

Note: FastAPI's `HTTPException(detail=...)` puts the detail value into the response body as `{"detail": ...}`. The test reads `r.json().get("errors", {})` — so we need the response shape to be `{"errors": {...}}`, not `{"detail": {"errors": {...}}}`. Adjust by returning a JSONResponse directly when there are errors:

```python
from fastapi.responses import JSONResponse

@router.put("/api/targets/{target}/scope", status_code=204)
def put_scope(target: str, body: ScopeBody):
    if not (_targets_root() / target).is_dir():
        raise HTTPException(404, detail=f"unknown target: {target!r}")
    errors = _validate_scope(body)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    path = _scope_path(target)
    path.write_text(_serialize_scope_toml(body))
    from fastapi import Response
    return Response(status_code=204)
```

(Pydantic body validation errors — e.g., wrong types — will be 422 automatically, separate from our 400 path.)

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_scope_routes.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite check**

Run: `pytest tests/gui_service/ -v 2>&1 | tail -5`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_scope_routes.py
git commit -m "feat(gui_service): GET/PUT /api/targets/{name}/scope for scope.toml editing

GET returns parsed scope.toml or defaults (exists: false). PUT validates
CIDRs via ipaddress.ip_network and IPs via ipaddress.ip_address; returns
400 with per-field errors on invalid input, 204 on successful write.
Empty arrays still produce a written file (explicit 'no constraints')."
```

---

## Task 2: Report GET + POST endpoints

Reuses the existing `_render_report(kb)` function in `tools/kb.py`. GET always renders fresh; POST snapshots to `targets/<t>/report.md`.

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Test: `tests/gui_service/test_report_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_report_routes.py`:

```python
"""GET + POST /api/targets/{name}/report renders/writes the engagement
report using the same _render_report function as the agent's
kb_export_report tool."""
import pytest
from pathlib import Path
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
async def test_get_report_renders_markdown(client):
    """A bare target dir still produces a valid report (empty sections)."""
    r = await client.get("/api/targets/10.10.10.5/report", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "10.10.10.5"
    assert "Penetration Test Report" in body["markdown"]
    assert body["bytes"] == len(body["markdown"].encode())
    assert "generated_at" in body


@pytest.mark.asyncio
async def test_post_report_writes_to_disk(client, tmp_path):
    r = await client.post("/api/targets/10.10.10.5/report", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    report_path = Path(body["path"])
    assert report_path.is_file()
    content = report_path.read_text()
    assert "Penetration Test Report" in content
    assert body["bytes"] == len(content.encode())


@pytest.mark.asyncio
async def test_report_404_for_unknown_target(client):
    r = await client.get("/api/targets/no-such/report", headers=HEADERS)
    assert r.status_code == 404
    r2 = await client.post("/api/targets/no-such/report", headers=HEADERS)
    assert r2.status_code == 404
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_report_routes.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement the endpoints**

Open `src/reverser/gui_service/routes/targets.py`. Add this import at the top:

```python
from datetime import datetime, timezone
from ...tools.kb import _render_report
```

Append the handlers after the scope routes:

```python
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
```

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_report_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_report_routes.py
git commit -m "feat(gui_service): GET/POST /api/targets/{name}/report

GET renders fresh Markdown via the existing _render_report(kb). POST
writes to targets/<t>/report.md and returns the absolute path. Always
fresh — never reads stale report.md on GET."
```

---

## Task 3: Screenshot list + image-bytes endpoints

Two endpoints — list + bytes — but they're tightly coupled. Same task.

**Files:**
- Modify: `src/reverser/gui_service/routes/targets.py`
- Test: `tests/gui_service/test_screenshot_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_screenshot_routes.py`:

```python
"""GET screenshot list + image bytes for one finding."""
import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    findings_dir = tmp_path / "targets" / "10.10.10.5" / "findings" / "f-42"
    findings_dir.mkdir(parents=True)
    (findings_dir / "screenshot-1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"X" * 64)
    (findings_dir / "screenshot-2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"Y" * 128)
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


@pytest.mark.asyncio
async def test_list_screenshots(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots",
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["finding_id"] == "f-42"
    indices = sorted(s["index"] for s in body["screenshots"])
    assert indices == [1, 2]
    by_index = {s["index"]: s for s in body["screenshots"]}
    # First file: 8-byte PNG header + 64 X bytes = 72 bytes
    assert by_index[1]["size_bytes"] == 72
    assert by_index[2]["size_bytes"] == 136
    assert "captured_at" in by_index[1]


@pytest.mark.asyncio
async def test_list_screenshots_404_for_unknown_finding(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/no-such/screenshots",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_image_bytes_returns_png(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots/1",
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_image_bytes_404_for_missing_index(client):
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots/99",
        headers=HEADERS,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_image_bytes_rejects_path_traversal(client):
    """The {n} path component must be digits only — a regex digit check
    is enforced before the path is joined."""
    r = await client.get(
        "/api/targets/10.10.10.5/findings/f-42/screenshots/..%2F..%2Fetc%2Fpasswd",
        headers=HEADERS,
    )
    # 404 from FastAPI route-not-found OR 422 from path validation —
    # both are acceptable (the point is: never 200 with /etc/passwd).
    assert r.status_code in (404, 422)
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_screenshot_routes.py -v`
Expected: FAIL — 404 (routes missing).

- [ ] **Step 3: Implement the endpoints**

Append to `src/reverser/gui_service/routes/targets.py`:

```python
from datetime import datetime, timezone
from fastapi.responses import FileResponse


def _findings_dir(target: str, finding_id: str):
    return _targets_root() / target / "findings" / finding_id


@router.get("/api/targets/{target}/findings/{finding_id}/screenshots")
def list_screenshots(target: str, finding_id: str) -> dict:
    d = _findings_dir(target, finding_id)
    if not d.is_dir():
        raise HTTPException(404, detail=f"unknown finding: {finding_id!r}")
    entries = []
    for f in sorted(d.glob("screenshot-*.png")):
        # Parse 'screenshot-<n>.png' → index integer.
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


# Path constraint: {n} is digits only — prevents path traversal at the
# routing layer. FastAPI's Path param doesn't honor regex by default, so
# we validate inside the handler.
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
```

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_screenshot_routes.py -v`
Expected: PASS (5 tests — the 4 spec tests plus the path-traversal test).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/routes/targets.py tests/gui_service/test_screenshot_routes.py
git commit -m "feat(gui_service): findings/{id}/screenshots list + image bytes endpoints

GET .../screenshots returns a list of {index, size_bytes, captured_at}
parsed from 'screenshot-<n>.png' files under
targets/<t>/findings/<id>/. GET .../screenshots/{n} serves the PNG bytes
with Content-Type: image/png. Path-traversal mitigation: {n} must be
digits — non-numeric paths return 404 before any filesystem access."
```

---

## Task 4: Frontend deps

**Files:**
- Modify: `desktop/package.json` (npm install adds the deps)

- [ ] **Step 1: Install**

```bash
cd desktop && npm install react-markdown@^9 remark-gfm@^4
```

- [ ] **Step 2: Compile-check**

```bash
cd desktop && npx tsc -b
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add desktop/package.json desktop/package-lock.json
git commit -m "build(desktop): add react-markdown + remark-gfm for the report tab"
```

---

## Task 5: API types + query hooks

**Files:**
- Modify: `desktop/renderer/src/api/client.ts`
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Append types to `client.ts`**

```ts
// ---- Phase 3b: Scope ----

export type ScopeBody = {
  in_scope_cidrs: string[];
  out_of_scope_ips: string[];
  allowed_hours: string | null;
  no_dos: boolean;
  no_account_lockout: boolean;
};

export type ScopeResponse = ScopeBody & { exists: boolean };

export type ScopeUpdateError = { errors: Record<string, string> };

// ---- Phase 3b: Report ----

export type ReportResponse = {
  target: string;
  markdown: string;
  generated_at: string;
  bytes: number;
};

export type ExportReportResponse = {
  target: string;
  path: string;
  bytes: number;
};

// ---- Phase 3b: Screenshots ----

export type ScreenshotEntry = {
  index: number;
  size_bytes: number;
  captured_at: string;
};

export type ScreenshotsResponse = {
  finding_id: string;
  screenshots: ScreenshotEntry[];
};
```

- [ ] **Step 2: Add hooks to `queries.ts`**

In the existing `import { ... } from "./client"` block, add: `ScopeBody`, `ScopeResponse`, `ReportResponse`, `ExportReportResponse`, `ScreenshotsResponse`.

Append at the bottom of the file:

```ts
export function useScope(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["scope", target],
    queryFn: () =>
      api.get<ScopeResponse>(`/api/targets/${encodeURIComponent(target!)}/scope`),
    enabled: ready && !!target,
    staleTime: 30_000,
  });
}

export function useUpdateScope(target: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScopeBody) =>
      api.put<void>(`/api/targets/${encodeURIComponent(target)}/scope`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scope", target] });
    },
  });
}

export function useReport(target: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["report", target],
    queryFn: () =>
      api.get<ReportResponse>(`/api/targets/${encodeURIComponent(target!)}/report`),
    enabled: ready && !!target,
    staleTime: 30_000,
  });
}

export function useExportReport(target: string) {
  return useMutation({
    mutationFn: () =>
      api.post<ExportReportResponse>(`/api/targets/${encodeURIComponent(target)}/report`),
  });
}

export function useScreenshots(target: string | null, findingId: string | null) {
  const ready = useReady();
  return useQuery({
    queryKey: ["screenshots", target, findingId],
    queryFn: () =>
      api.get<ScreenshotsResponse>(
        `/api/targets/${encodeURIComponent(target!)}/findings/${encodeURIComponent(findingId!)}/screenshots`,
      ),
    enabled: ready && !!target && !!findingId,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 3: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/api/
git commit -m "feat(desktop): types + query hooks for scope/report/screenshots endpoints"
```

---

## Task 6: Shared `FindingRow` component

Extracts the finding-row rendering currently inlined in `KBTabbedView.tsx` (and similar markup in `FindingsPane.tsx`). Adds the `📷 N` evidence badge.

**Files:**
- Create: `desktop/renderer/src/components/FindingRow.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { Camera } from "lucide-react";
import { useScreenshots } from "@/api/queries";
import { cn } from "@/lib/utils";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-blue-400",
  info: "text-neutral-500",
};

type FindingLike = Record<string, unknown> & {
  id?: string | number;
  severity?: string;
  title?: string;
  description?: string;
};

export function FindingRow({
  target,
  finding,
  onClickEvidence,
}: {
  /** Used to look up screenshots for the badge. Pass null to disable the badge. */
  target: string | null;
  finding: FindingLike;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}) {
  const findingId = String(finding.id ?? "");
  const screenshots = useScreenshots(target, findingId || null);
  const screenshotCount = screenshots.data?.screenshots.length ?? 0;
  const sev = String(finding.severity ?? "info").toLowerCase();

  return (
    <div className="border border-neutral-800 rounded p-2 bg-neutral-950">
      <div className="flex items-center gap-2">
        <span className={SEVERITY_COLOR[sev] ?? "text-neutral-500"}>● {sev}</span>
        <span className="text-neutral-200">{String(finding.title ?? "—")}</span>
        {screenshotCount > 0 && onClickEvidence && findingId && (
          <button
            onClick={() => onClickEvidence(findingId, 1)}
            className={cn(
              "ml-auto inline-flex items-center gap-1 text-[10px]",
              "px-1.5 py-0.5 rounded border border-neutral-700 hover:bg-neutral-800",
              "text-neutral-300 hover:text-neutral-100 transition-colors",
            )}
            title={`${screenshotCount} screenshot${screenshotCount === 1 ? "" : "s"}`}
          >
            <Camera className="w-3 h-3" />
            {screenshotCount}
          </button>
        )}
      </div>
      {!!finding.description && (
        <p className="text-neutral-400 text-xs mt-1 line-clamp-3">
          {String(finding.description)}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/components/FindingRow.tsx
git commit -m "feat(desktop): shared FindingRow with screenshot count badge

Replaces inline finding-row rendering from FindingsPane and
KBTabbedView's Findings tab. Adds a [camera] N badge when the finding
has screenshots; clicking it fires onClickEvidence(findingId, 1)."
```

---

## Task 7: `ScreenshotLightboxModal`

**Files:**
- Create: `desktop/renderer/src/modals/ScreenshotLightboxModal.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useScreenshots } from "@/api/queries";
import { useConnection } from "@/state/connection";

export function ScreenshotLightboxModal({
  target,
  findingId,
  startIndex = 1,
  open,
  onOpenChange,
}: {
  target: string;
  findingId: string;
  startIndex?: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const screenshots = useScreenshots(target, findingId);
  const [currentIndex, setCurrentIndex] = useState(startIndex);

  // Connection info for building the image URL.
  const port = useConnection((s) => s.port);
  const token = useConnection((s) => s.token);

  useEffect(() => {
    if (open) setCurrentIndex(startIndex);
  }, [open, startIndex]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
      if (e.key === "ArrowLeft") setCurrentIndex((i) => Math.max(1, i - 1));
      if (e.key === "ArrowRight") {
        const max = screenshots.data?.screenshots.length ?? 1;
        setCurrentIndex((i) => Math.min(max, i + 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange, screenshots.data]);

  if (!open) return null;

  const entries = screenshots.data?.screenshots ?? [];
  const total = entries.length;
  const safeIndex = Math.min(Math.max(currentIndex, 1), Math.max(total, 1));

  // Build the image URL with the bearer token as a query param. We can't
  // set Authorization headers on a regular <img> request; the server
  // accepts ?token=… on this endpoint by adding a small middleware
  // affordance — OR we use fetch + blob URL. Simplest: fetch + blob.
  const imgUrl = port && token && target && findingId
    ? `http://127.0.0.1:${port}/api/targets/${encodeURIComponent(target)}/findings/${encodeURIComponent(findingId)}/screenshots/${safeIndex}`
    : null;
  const [imgBlobUrl, setImgBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!imgUrl || !token) { setImgBlobUrl(null); return; }
    let cancelled = false;
    let prevBlob: string | null = null;
    fetch(imgUrl, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.blob())
      .then((b) => {
        if (cancelled) return;
        const url = URL.createObjectURL(b);
        setImgBlobUrl((prev) => { prevBlob = prev; return url; });
      })
      .catch(() => { if (!cancelled) setImgBlobUrl(null); });
    return () => {
      cancelled = true;
      if (prevBlob) URL.revokeObjectURL(prevBlob);
    };
  }, [imgUrl, token]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/90"
      onClick={() => onOpenChange(false)}
    >
      <button
        className="absolute top-4 right-4 text-neutral-300 hover:text-white"
        onClick={() => onOpenChange(false)}
      >
        <X className="w-6 h-6" />
      </button>

      <div className="absolute top-4 left-4 text-xs text-neutral-400 font-mono">
        screenshot {safeIndex} of {total} · finding {findingId}
      </div>

      <div className="flex items-center justify-center gap-4 w-full max-w-[95vw] max-h-[90vh]">
        <button
          disabled={safeIndex <= 1}
          onClick={(e) => { e.stopPropagation(); setCurrentIndex((i) => Math.max(1, i - 1)); }}
          className="text-neutral-400 hover:text-white disabled:opacity-30"
        >
          <ChevronLeft className="w-8 h-8" />
        </button>
        <div
          className="max-w-[80vw] max-h-[85vh] flex items-center justify-center"
          onClick={(e) => e.stopPropagation()}
        >
          {imgBlobUrl ? (
            <img
              src={imgBlobUrl}
              alt={`Screenshot ${safeIndex}`}
              className="max-w-full max-h-full object-contain"
            />
          ) : (
            <p className="text-neutral-500 text-sm">loading…</p>
          )}
        </div>
        <button
          disabled={safeIndex >= total}
          onClick={(e) => { e.stopPropagation(); setCurrentIndex((i) => Math.min(total, i + 1)); }}
          className="text-neutral-400 hover:text-white disabled:opacity-30"
        >
          <ChevronRight className="w-8 h-8" />
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/modals/ScreenshotLightboxModal.tsx
git commit -m "feat(desktop): ScreenshotLightboxModal shared evidence viewer

Fetches the screenshot list, displays one at a time, prev/next nav
(arrows + arrow keys), escape closes. Uses fetch + blob URL so the
bearer token can be sent as an Authorization header (a regular <img>
tag can't set headers)."
```

---

## Task 8: `ScopeEditorModal`

**Files:**
- Create: `desktop/renderer/src/modals/ScopeEditorModal.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useEffect, useState } from "react";
import {
  Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useScope, useUpdateScope } from "@/api/queries";
import { ApiError } from "@/api/client";

export function ScopeEditorModal({
  target,
  open,
  onOpenChange,
}: {
  target: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const scope = useScope(target);
  const update = useUpdateScope(target);

  const [cidrsText, setCidrsText] = useState("");
  const [ipsText, setIpsText] = useState("");
  const [hours, setHours] = useState("");
  const [noDos, setNoDos] = useState(false);
  const [noLockout, setNoLockout] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Seed form when modal opens or scope data changes.
  useEffect(() => {
    if (!open || !scope.data) return;
    setCidrsText(scope.data.in_scope_cidrs.join("\n"));
    setIpsText(scope.data.out_of_scope_ips.join("\n"));
    setHours(scope.data.allowed_hours ?? "");
    setNoDos(scope.data.no_dos);
    setNoLockout(scope.data.no_account_lockout);
    setErrors({});
  }, [open, scope.data]);

  const submit = async () => {
    setErrors({});
    const body = {
      in_scope_cidrs: cidrsText.split("\n").map((s) => s.trim()).filter(Boolean),
      out_of_scope_ips: ipsText.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_hours: hours.trim() || null,
      no_dos: noDos,
      no_account_lockout: noLockout,
    };
    try {
      await update.mutateAsync(body);
      onOpenChange(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        const body = e.body as { errors?: Record<string, string> } | null;
        setErrors(body?.errors ?? {});
      } else {
        alert((e as Error).message);
      }
    }
  };

  // Helpers to surface per-line errors next to the textarea.
  const cidrErrors = Object.entries(errors).filter(([k]) => k.startsWith("in_scope_cidrs"));
  const ipErrors = Object.entries(errors).filter(([k]) => k.startsWith("out_of_scope_ips"));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Edit scope · {target}</DialogTitle>
        <DialogDescription>
          Constrains every offensive tool. Writes <code className="font-mono">scope.toml</code> to the target directory.
          CIDRs and IPs are validated server-side.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4 my-3">
        <div>
          <label className="block text-xs text-neutral-500 mb-1">in_scope_cidrs (one per line)</label>
          <Textarea
            value={cidrsText}
            onChange={(e) => setCidrsText(e.target.value)}
            rows={3}
            placeholder="10.10.10.0/24"
          />
          {cidrErrors.map(([k, v]) => (
            <p key={k} className="text-[10px] text-red-400 mt-1 font-mono">{k}: {v}</p>
          ))}
        </div>
        <div>
          <label className="block text-xs text-neutral-500 mb-1">out_of_scope_ips (one per line)</label>
          <Textarea
            value={ipsText}
            onChange={(e) => setIpsText(e.target.value)}
            rows={3}
            placeholder="10.10.10.99"
          />
          {ipErrors.map(([k, v]) => (
            <p key={k} className="text-[10px] text-red-400 mt-1 font-mono">{k}: {v}</p>
          ))}
        </div>
        <div>
          <label className="block text-xs text-neutral-500 mb-1">allowed_hours (freeform string)</label>
          <Input
            value={hours}
            onChange={(e) => setHours(e.target.value)}
            placeholder="09:00-17:00 UTC"
          />
        </div>
        <div className="flex gap-4 text-xs">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={noDos} onChange={(e) => setNoDos(e.target.checked)} />
            <span>no_dos</span>
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={noLockout} onChange={(e) => setNoLockout(e.target.checked)} />
            <span>no_account_lockout</span>
          </label>
        </div>
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button onClick={submit} disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/modals/ScopeEditorModal.tsx
git commit -m "feat(desktop): ScopeEditorModal — form for scope.toml fields

Textareas for CIDRs and IPs (one per line, easy copy-paste). Text input
for allowed_hours. Checkboxes for no_dos + no_account_lockout. On 400
from the PUT, surfaces per-field errors inline above the offending
textarea."
```

---

## Task 9: `ReportTab` + wire into `KBTabbedView`

The KBTabbedView already exists with 5 tabs. This task adds a 6th tab (Report) AND switches the inline finding-row rendering to use the shared `FindingRow` component AND wires the lightbox modal.

**Files:**
- Create: `desktop/renderer/src/panes/ReportTab.tsx`
- Modify: `desktop/renderer/src/components/KBTabbedView.tsx`

- [ ] **Step 1: Create `ReportTab.tsx`**

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useReport, useExportReport } from "@/api/queries";
import { Button } from "@/components/ui/button";

export function ReportTab({ target }: { target: string | null }) {
  const { data, isLoading, error } = useReport(target);
  const exportMutation = useExportReport(target ?? "");

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">rendering report…</p>;
  if (error) return <p className="p-3 text-xs text-red-400">{String((error as Error).message)}</p>;
  if (!data) return null;

  const onExport = async () => {
    try {
      const res = await exportMutation.mutateAsync();
      alert(`Saved to ${res.path}`);
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 overflow-auto p-4">
        <article className="prose prose-invert prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.markdown}</ReactMarkdown>
        </article>
      </div>
      <div className="border-t border-neutral-800 px-3 py-2 flex items-center text-[10px] text-neutral-500 font-mono">
        <span>Generated {data.generated_at} · {data.bytes} bytes</span>
        <Button
          size="sm" variant="outline"
          className="ml-auto"
          onClick={onExport}
          disabled={exportMutation.isPending}
        >
          {exportMutation.isPending ? "Saving…" : "Export to disk"}
        </Button>
      </div>
    </div>
  );
}
```

(`prose` classes come from Tailwind Typography. If the project doesn't have `@tailwindcss/typography` installed, fall back to plain styling: drop the `prose` classes, the renderer will produce unstyled HTML which is still readable.)

- [ ] **Step 2: Verify Tailwind Typography availability**

```bash
cd desktop && grep -E "@tailwindcss/typography" package.json tailwind.config.* 2>&1 | head -5
```

If not installed, either install it (`npm install -D @tailwindcss/typography` and add to `tailwind.config.ts` plugins) OR drop the `prose` classes from the JSX. **Default action: drop the classes** to avoid scope creep — replace the `<article className="prose prose-invert prose-sm max-w-none">` with `<article className="text-sm text-neutral-200 max-w-none [&_h1]:text-base [&_h1]:font-medium [&_h1]:mt-4 [&_h1]:mb-2 [&_h2]:text-sm [&_h2]:font-medium [&_h2]:mt-3 [&_h2]:mb-1 [&_p]:my-2 [&_table]:text-xs [&_table]:border [&_table]:border-neutral-800 [&_th]:px-2 [&_th]:py-1 [&_th]:border-b [&_th]:border-neutral-800 [&_td]:px-2 [&_td]:py-1 [&_td]:border-b [&_td]:border-neutral-900 [&_code]:font-mono [&_code]:text-amber-300 [&_pre]:bg-neutral-900 [&_pre]:p-2 [&_pre]:rounded">`. Long, but explicit and dep-free.

- [ ] **Step 3: Update `KBTabbedView.tsx`**

Read the existing file first:

```bash
cat /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop/renderer/src/components/KBTabbedView.tsx
```

The current tabs array is `["findings", "hypotheses", "hosts", "services", "credentials"]`. Add `"report"`. The `FindingsTable` inline component is replaced by mapping through the new `FindingRow` shared component. A new prop `onClickEvidence` is added to `KBTabbedView` and passed through.

Full replacement:

```tsx
import { useState } from "react";
import { useTargetKB } from "@/api/queries";
import { cn } from "@/lib/utils";
import { FindingRow } from "@/components/FindingRow";
import { ReportTab } from "@/panes/ReportTab";

type Tab = "findings" | "hypotheses" | "hosts" | "services" | "credentials" | "report";

const TABS: Tab[] = ["findings", "hypotheses", "hosts", "services", "credentials", "report"];

export function KBTabbedView({
  target,
  onClickEvidence,
}: {
  target: string | null;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("findings");
  const { data, isLoading } = useTargetKB(target);

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;

  return (
    <div className="flex flex-col h-full">
      <div className="flex gap-3 px-3 border-b border-neutral-800 text-[10px] uppercase tracking-wide h-7 items-center">
        {TABS.map((t) => {
          const count = t === "report" ? null : ((data?.[t as keyof typeof data] ?? []) as unknown[]).length;
          return (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "transition-colors",
                t === tab ? "text-neutral-200" : "text-neutral-500 hover:text-neutral-300",
              )}
            >
              {t}{count !== null ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {tab === "report" ? (
          <ReportTab target={target} />
        ) : isLoading ? (
          <p className="p-3 text-xs text-neutral-500">loading…</p>
        ) : (
          <div className="p-2">
            <TabContent
              tab={tab}
              data={data}
              target={target}
              onClickEvidence={onClickEvidence}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function TabContent({
  tab,
  data,
  target,
  onClickEvidence,
}: {
  tab: Tab;
  data: any;
  target: string;
  onClickEvidence?: (findingId: string, startIndex: number) => void;
}) {
  if (tab === "report") return null;
  const rows = (data?.[tab] ?? []) as Array<Record<string, unknown>>;
  if (rows.length === 0) return <p className="text-xs text-neutral-500">empty</p>;
  if (tab === "findings") {
    const sorted = rows.slice().sort((a, b) => {
      const SEV_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
      const av = SEV_ORDER[String(a.severity ?? "info").toLowerCase()] ?? 99;
      const bv = SEV_ORDER[String(b.severity ?? "info").toLowerCase()] ?? 99;
      return av - bv;
    });
    return (
      <div className="space-y-2 text-xs">
        {sorted.map((f, i) => (
          <FindingRow
            key={i}
            target={target}
            finding={f}
            onClickEvidence={onClickEvidence}
          />
        ))}
      </div>
    );
  }
  if (tab === "hypotheses") {
    const STATUS_COLOR: Record<string, string> = {
      confirmed: "text-green-400",
      testing: "text-amber-400",
      proposed: "text-neutral-400",
      refuted: "text-red-400",
      abandoned: "text-neutral-600",
    };
    return (
      <div className="space-y-1 text-xs font-mono">
        {rows.map((h, i) => {
          const status = String(h.status ?? "proposed").toLowerCase();
          return (
            <div key={i} className="border border-neutral-800 rounded p-2 bg-neutral-950">
              <div className="flex items-center gap-2">
                <span className={STATUS_COLOR[status] ?? "text-neutral-400"}>● {status}</span>
                <span className="text-neutral-200 truncate">
                  {String(h.statement ?? h.title ?? "—")}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    );
  }
  // Generic fallback for hosts/services/credentials.
  return (
    <div className="space-y-1 text-[10px] font-mono">
      {rows.slice(0, 200).map((r, i) => (
        <pre key={i} className="text-neutral-400 truncate" title={JSON.stringify(r)}>
          {JSON.stringify(r)}
        </pre>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/panes/ReportTab.tsx desktop/renderer/src/components/KBTabbedView.tsx
git commit -m "feat(desktop): ReportTab + KBTabbedView shared FindingRow

New 'Report' tab on KBTabbedView renders Markdown via react-markdown +
remark-gfm, with an 'Export to disk' button that POSTs and toasts the
saved path. Findings tab switches to the shared FindingRow component
so it gets the screenshot badge + lightbox callback. KBTabbedView's
onClickEvidence prop is forwarded down to FindingRow."
```

---

## Task 10: `FindingsPane` uses shared `FindingRow`

The live-session right rail. Needs the same `FindingRow` + lightbox treatment.

**Files:**
- Modify: `desktop/renderer/src/panes/FindingsPane.tsx`

- [ ] **Step 1: Read the existing file**

```bash
cat /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop/renderer/src/panes/FindingsPane.tsx
```

The current implementation renders findings inline. Replace with:

```tsx
import { useState } from "react";
import { useTargetKB } from "@/api/queries";
import { FindingRow } from "@/components/FindingRow";
import { ScreenshotLightboxModal } from "@/modals/ScreenshotLightboxModal";

export function FindingsPane({ target }: { target: string | null }) {
  const { data, isLoading } = useTargetKB(target);
  const [lightbox, setLightbox] = useState<{ findingId: string; startIndex: number } | null>(null);

  if (!target) return <p className="p-3 text-xs text-neutral-500">no target</p>;
  if (isLoading) return <p className="p-3 text-xs text-neutral-500">loading…</p>;

  const findings = (data?.findings ?? []) as Array<Record<string, unknown>>;
  if (findings.length === 0) return <p className="p-3 text-xs text-neutral-500">no findings yet</p>;

  return (
    <>
      <div className="p-2 space-y-2 text-xs">
        {findings.map((f, i) => (
          <FindingRow
            key={i}
            target={target}
            finding={f}
            onClickEvidence={(findingId, startIndex) =>
              setLightbox({ findingId, startIndex })
            }
          />
        ))}
      </div>
      {lightbox && (
        <ScreenshotLightboxModal
          target={target}
          findingId={lightbox.findingId}
          startIndex={lightbox.startIndex}
          open={true}
          onOpenChange={(open) => { if (!open) setLightbox(null); }}
        />
      )}
    </>
  );
}
```

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/panes/FindingsPane.tsx
git commit -m "feat(desktop): FindingsPane uses shared FindingRow + ScreenshotLightboxModal"
```

---

## Task 11: TargetOverview wires "Edit scope" + lightbox

Adds the "Edit scope" button to the TargetOverview header that opens `ScopeEditorModal`. Wires the lightbox into `KBTabbedView`'s `onClickEvidence` callback.

**Files:**
- Modify: `desktop/renderer/src/pages/TargetOverview.tsx`

- [ ] **Step 1: Update the page**

Read the existing file:

```bash
cat /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop/renderer/src/pages/TargetOverview.tsx
```

The current file has a header with the target name and a "New engagement" button. Add the "Edit scope" button next to it; mount `ScopeEditorModal` and `ScreenshotLightboxModal` at the bottom; pass `onClickEvidence` into `KBTabbedView`.

Replace the file:

```tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useSessions, useTargetSummary } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SessionRow } from "@/components/SessionRow";
import { KBTabbedView } from "@/components/KBTabbedView";
import { ScopeEditorModal } from "@/modals/ScopeEditorModal";
import { ScreenshotLightboxModal } from "@/modals/ScreenshotLightboxModal";

export function TargetOverview() {
  const { name: rawName } = useParams<{ name: string }>();
  const name = rawName ? decodeURIComponent(rawName) : null;
  const summary = useTargetSummary(name);
  const sessions = useSessions();
  const targetSessions = (sessions.data?.sessions ?? []).filter(
    (s) => s.target === name,
  );
  const [scopeOpen, setScopeOpen] = useState(false);
  const [lightbox, setLightbox] = useState<{ findingId: string; startIndex: number } | null>(null);

  if (!name) return null;

  return (
    <div className="p-6 h-full overflow-auto">
      <div className="flex items-center mb-4 gap-3">
        <h2 className="text-base font-medium text-neutral-100">{name}</h2>
        <Button size="sm" variant="outline" onClick={() => setScopeOpen(true)}>
          Edit scope
        </Button>
        <Link to={`/new?target=${encodeURIComponent(name)}`} className="ml-auto">
          <Button size="sm">New engagement</Button>
        </Link>
      </div>

      <Card className="mb-4">
        <CardHeader><CardTitle>Summary</CardTitle></CardHeader>
        <CardContent className="text-xs font-mono">
          {summary.isLoading && <p className="text-neutral-500">loading…</p>}
          {summary.error && (
            <p className="text-red-400">
              {String((summary.error as Error).message)}
            </p>
          )}
          {summary.data && (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <div>
                <span className="text-neutral-500">sessions: </span>
                <span className="text-neutral-200">
                  {summary.data.sessions.total}
                </span>
                <span className="text-neutral-500">
                  {" "}({summary.data.sessions.by_state.active} active ·{" "}
                  {summary.data.sessions.by_state.stopped} stopped ·{" "}
                  {summary.data.sessions.by_state.completed} done)
                </span>
              </div>
              <div>
                <span className="text-neutral-500">total spend: </span>
                <span className="text-neutral-200">
                  ${summary.data.spend.total_usd.toFixed(2)}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">first activity: </span>
                <span className="text-neutral-200">
                  {summary.data.first_activity ?? "—"}
                </span>
              </div>
              <div>
                <span className="text-neutral-500">last activity: </span>
                <span className="text-neutral-200">
                  {summary.data.last_activity ?? "—"}
                </span>
              </div>
              <div className="col-span-2">
                <span className="text-neutral-500">profiles used: </span>
                <span className="text-neutral-200">
                  {summary.data.profiles_used.join(", ") || "—"}
                </span>
              </div>
              <div className="col-span-2">
                <span className="text-neutral-500">KB: </span>
                <span className="text-neutral-200">
                  {summary.data.kb_counts.hosts} hosts ·{" "}
                  {summary.data.kb_counts.services} services ·{" "}
                  {summary.data.kb_counts.credentials} creds ·{" "}
                  {summary.data.kb_counts.findings} findings ·{" "}
                  {summary.data.kb_counts.hypotheses} hypotheses
                </span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>Sessions</CardTitle></CardHeader>
          <CardContent className="p-0">
            {targetSessions.length === 0 ? (
              <p className="p-3 text-xs text-neutral-500">no sessions for this target</p>
            ) : (
              targetSessions.map((s) => <SessionRow key={s.id} session={s} />)
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Knowledge base</CardTitle></CardHeader>
          <CardContent className="p-0 h-[480px]">
            <KBTabbedView
              target={name}
              onClickEvidence={(findingId, startIndex) =>
                setLightbox({ findingId, startIndex })
              }
            />
          </CardContent>
        </Card>
      </div>

      <ScopeEditorModal target={name} open={scopeOpen} onOpenChange={setScopeOpen} />
      {lightbox && (
        <ScreenshotLightboxModal
          target={name}
          findingId={lightbox.findingId}
          startIndex={lightbox.startIndex}
          open={true}
          onOpenChange={(open) => { if (!open) setLightbox(null); }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/pages/TargetOverview.tsx
git commit -m "feat(desktop): TargetOverview gets Edit scope button + ScopeEditorModal + lightbox

Header now has 'Edit scope' next to the target name (opens
ScopeEditorModal). The KB right column wires onClickEvidence into the
ScreenshotLightboxModal."
```

---

## Task 12: Playwright e2e

**File:**
- Create: `desktop/tests/e2e/phase3b.spec.ts`

- [ ] **Step 1: Build**

```bash
cd desktop && npm run build 2>&1 | tail -3
```

Expected: clean.

- [ ] **Step 2: Write the spec**

```ts
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// Phase 3b structural tests: confirm the new components mount without
// breaking the existing flow. Real fixture-driven assertions (Edit scope
// modal opens, Report tab renders content) require an actual target
// directory with KB content; tests are scoped to UI presence.

test("targets panel still renders after Phase 3b refactor", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click('[title="Targets"]');
    await expect(w.locator("text=Targets").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=by activity")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("sessions panel still renders after Phase 3b refactor", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.click('[title="Sessions"]');
    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 5_000 });
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("profile grid still renders (react-markdown import didn't break)", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Profiles").first()).toBeVisible({ timeout: 30_000 });
    const cards = w.locator(".grid > div");
    await expect(async () => {
      const count = await cards.count();
      expect(count).toBeGreaterThanOrEqual(10);
    }).toPass({ timeout: 30_000 });
  } finally {
    await app.close();
  }
});

test("legacy /session/:id still redirects (regression check)", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      REVERSER_PENTEST_AUTHORIZED: "1",
      PATH: `${path.join(__dirname, "bin")}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const w = await app.firstWindow();
    await expect(w.locator("text=Dashboard").first()).toBeVisible({ timeout: 30_000 });
    await w.evaluate(() => {
      window.history.pushState({}, "", "/session/legacy-id");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await w.waitForFunction(
      () => window.location.pathname === "/sessions/legacy-id",
      { timeout: 5_000 },
    );
  } finally {
    await app.close();
  }
});
```

- [ ] **Step 3: Run**

```bash
cd desktop && PYTHONPATH=$(pwd)/../src npx playwright test 2>&1 | tail -15
```

Expected: 13 passed (9 existing + 4 new).

- [ ] **Step 4: Commit**

```bash
git add desktop/tests/e2e/phase3b.spec.ts
git commit -m "test(desktop): e2e — Phase 3b regression coverage (4 tests)

Structural tests: targets panel, sessions panel, profile grid still
mount (no regressions from the new components, the FindingRow refactor,
or the react-markdown dep). Real modal/lightbox/edit-scope behavior
needs fixture data and lands when we have a per-target test harness."
```

---

## Verification

After all 12 tasks:

```bash
# Backend
PYTHONPATH=src python3 -m pytest tests/ 2>&1 | tail -5
# Expected: new tests pass (~13 new); pre-existing handshake smoke may still fail (env-fragile, not a regression).

# Frontend
cd desktop && npx tsc -b && npx tsc -p tsconfig.electron.json && npm run build

# E2E
cd desktop && PYTHONPATH=$(pwd)/../src npx playwright test
# Expected: 13 passed.
```

Manual smoke (in devenv shell with a target that has KB content):

1. Navigate to `/target/<name>` via the Targets panel.
2. Click **Edit scope** → modal opens with empty fields (or pre-filled if scope.toml exists).
3. Add `not-a-cidr` to in_scope_cidrs, click Save → see inline error.
4. Save valid CIDRs → modal closes; PUT writes `targets/<name>/scope.toml`.
5. Open KB tabbed view → click **Report** tab → Markdown renders.
6. Click **Export to disk** → alert: "Saved to .../report.md".
7. (If the target has a finding with screenshots) Findings tab → click `📷 N` badge → lightbox opens; arrow keys navigate; ESC closes.

## Risks observed

- **`prose` class fallback in ReportTab**: if Tailwind Typography isn't installed, the fallback long-form class string in §Task 9 produces a readable (if minimal) render. Production polish would install `@tailwindcss/typography` and use proper `prose-invert prose-sm`. Documented; not a blocker.
- **Per-finding screenshot count is an N+1 fetch**: TanStack Query dedupes; <20 findings per page is well within budget. Phase 4 can batch via a single endpoint if profiling indicates.
- **Manual TOML writer**: 5 fixed fields, no quoting edge cases other than strings. Round-trip-tested via the read-after-write test.
- **Path traversal on image bytes**: `n` is validated as digits-only before the path is joined. Tested.

## What this plan does NOT cover

- Drag-drop scope.toml import — out of scope.
- Live report auto-refresh on KB updates — out (tab refetch only).
- Bulk report export across targets — Phase 4.
- Screenshot editing/annotation — out.
- Diff between two report exports — out.
- Tailwind Typography install — deferred to a polish pass.
- BloodHound graph view — Phase 3c.
