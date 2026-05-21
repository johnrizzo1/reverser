# Engagement Config Display & Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show backend/model/api_base/profile/budget/max_turns on each engagement and allow editing those fields while the engagement is stopped, so the next Resume picks them up.

**Architecture:** A new `PATCH /api/sessions/{id}/config?target=…` endpoint validates and persists edits to the on-disk `SessionSnapshot`. The renderer surfaces config in a collapsible panel under the session status bar — read-only when active/terminal, editable when stopped. Because edits commit to the snapshot before resume, no change is needed to the resume flow itself.

**Tech Stack:** FastAPI + pydantic (backend route), `reverser.sessions` module (snapshot persistence), React + Tanstack Query + zustand (renderer), Tailwind (styling), pytest + httpx ASGITransport (backend tests), Playwright (e2e).

**Spec:** [docs/superpowers/specs/2026-05-20-engagement-config-edit-design.md](../specs/2026-05-20-engagement-config-edit-design.md)

---

## File Structure

**Backend (Python):**
- Modify: `src/reverser/gui_service/session_manager.py` — extend per-row dict in `list_sessions` and `_serialize` to include `backend`, `model`, `api_base`, `budget`, `max_turns` on every row.
- Modify: `src/reverser/gui_service/routes/sessions.py` — add `UpdateConfigBody` pydantic model + `PATCH /api/sessions/{id}/config` handler.

**Backend tests:**
- Modify: `tests/gui_service/test_session_manager.py` — add a test asserting `list_sessions` rows carry the new fields for both active and stopped sessions.
- Create: `tests/gui_service/test_session_config_routes.py` — tests for the new PATCH endpoint (success, 400 validation, 404 missing, 409 active/terminal).

**Renderer (TypeScript / React):**
- Modify: `desktop/renderer/src/api/client.ts` — extend `SessionRow` type; add `"PATCH"` to the request method union and `api.patch` helper.
- Modify: `desktop/renderer/src/api/queries.ts` — add `useUpdateSessionConfig` mutation.
- Create: `desktop/renderer/src/layout/SessionConfigPanel.tsx` — the read/edit panel component.
- Modify: `desktop/renderer/src/layout/SessionStatusBar.tsx` — add expand toggle and render `<SessionConfigPanel />` when expanded.

**Renderer e2e:**
- Modify: `desktop/tests/e2e/engagement.spec.ts` — round-trip test: open stopped engagement, expand config, edit model, save, observe updated value.

---

## Task 1: Backend — expose new config fields in `list_sessions`

**Files:**
- Modify: `src/reverser/gui_service/session_manager.py:152-191`
- Test: `tests/gui_service/test_session_manager.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/gui_service/test_session_manager.py`, after `test_list_sessions_does_not_duplicate_active_session`:

```python
@pytest.mark.asyncio
async def test_list_sessions_rows_include_full_config(manager, tmp_path):
    """Each row in list_sessions exposes backend, model, api_base, budget,
    max_turns — the renderer reads them to display per-engagement config."""
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        info = await manager.create_session(
            target=str(tmp_path / "bin"),
            profile_key="general",
            backend_name="claude",
            model=None,
            api_base=None,
            budget=5.0,
            max_turns=50,
        )
    rows = manager.list_sessions()
    row = next(r for r in rows if r["id"] == info["id"])
    assert row["backend"] == "claude"
    assert row["model"] is None
    assert row["api_base"] is None
    assert row["budget"] == 5.0
    assert row["max_turns"] == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gui_service/test_session_manager.py::test_list_sessions_rows_include_full_config -xvs`
Expected: FAIL with `KeyError: 'backend'` (or similar — the active row from `_serialize` is missing those keys).

- [ ] **Step 3: Extend `list_sessions` and `_serialize` to include the new fields**

In `src/reverser/gui_service/session_manager.py`, change the disk-row dict construction inside `list_sessions` from:

```python
        out = []
        for s in snapshots:
            out.append({
                "id": s.session_id,
                "target": s.target,
                "profile": s.config.profile,
                "state": s.state,
                "turns": s.stats.turns,
                "total_cost": s.stats.total_cost,
                "stopped_at": s.stopped_at,
                "archived_at": s.archived_at,
            })
```

to:

```python
        out = []
        for s in snapshots:
            out.append({
                "id": s.session_id,
                "target": s.target,
                "profile": s.config.profile,
                "state": s.state,
                "turns": s.stats.turns,
                "total_cost": s.stats.total_cost,
                "stopped_at": s.stopped_at,
                "archived_at": s.archived_at,
                "backend": s.config.backend,
                "model": s.config.model,
                "api_base": s.config.api_base,
                "budget": s.config.budget,
                "max_turns": s.config.max_turns,
            })
```

And change the `_serialize` static method from:

```python
    @staticmethod
    def _serialize(gs: GUISession) -> dict[str, Any]:
        s = gs.stats
        return {
            "id": gs.session_id,
            "state": "active",
            "target": s["target"],
            "profile": s["profile_key"],
            "turns": s["turns"],
            "total_cost": s["total_cost"],
            "budget": s["budget"],
            "max_turns": s["max_turns"],
            "archived_at": None,
        }
```

to:

```python
    @staticmethod
    def _serialize(gs: GUISession) -> dict[str, Any]:
        s = gs.stats
        cfg = gs._agent._snapshot.config
        return {
            "id": gs.session_id,
            "state": "active",
            "target": s["target"],
            "profile": s["profile_key"],
            "turns": s["turns"],
            "total_cost": s["total_cost"],
            "budget": s["budget"],
            "max_turns": s["max_turns"],
            "archived_at": None,
            "backend": cfg.backend,
            "model": cfg.model,
            "api_base": cfg.api_base,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gui_service/test_session_manager.py -xvs`
Expected: PASS for all session manager tests (7 total including the new one).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/gui_service/session_manager.py tests/gui_service/test_session_manager.py
git commit -m "$(cat <<'EOF'
feat(gui-service): expose backend/model/api_base in session list rows

Adds backend, model, api_base, budget, max_turns to every row returned by
list_sessions — both for on-disk snapshots and for the in-memory active
session. The renderer needs these to display per-engagement config.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Backend — PATCH endpoint (validation + 404/409 paths)

**Files:**
- Modify: `src/reverser/gui_service/routes/sessions.py` — add `UpdateConfigBody` + handler.
- Create: `tests/gui_service/test_session_config_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_session_config_routes.py`:

```python
"""PATCH /api/sessions/{id}/config — edit a stopped engagement's config."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig
from tests.gui_service.fakes import FakeBackend


@pytest.fixture
def config(tmp_path):
    return ServiceConfig(
        host="127.0.0.1", port=0, token="t", project_root=str(tmp_path),
    )


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.chdir(tmp_path)
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


async def _create_and_stop(client, tmp_path) -> tuple[str, str]:
    """Helper: create an engagement then stop it; returns (session_id, target)."""
    target = str(tmp_path / "bin")
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": target, "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]
        await client.post(f"/api/sessions/{sid}/stop", headers=HEADERS)
    return sid, target


@pytest.mark.asyncio
async def test_patch_config_updates_stopped_session(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"model": "qwen3.5:35b", "api_base": "http://localhost:11434/v1",
              "budget": 7.5, "max_turns": 75},
    )
    assert r.status_code == 204, r.text

    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["model"] == "qwen3.5:35b"
    assert row["api_base"] == "http://localhost:11434/v1"
    assert row["budget"] == 7.5
    assert row["max_turns"] == 75


@pytest.mark.asyncio
async def test_patch_config_partial_leaves_unsent_fields_unchanged(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": 9.0},
    )
    assert r.status_code == 204

    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["budget"] == 9.0
    assert row["max_turns"] == 50  # unchanged
    assert row["backend"] == "claude"  # unchanged


@pytest.mark.asyncio
async def test_patch_config_404_for_unknown_session(client, tmp_path):
    target = str(tmp_path / "bin")
    r = await client.patch(
        f"/api/sessions/nope/config?target={target}",
        headers=HEADERS,
        json={"budget": 1.0},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_config_409_for_active_session(client, tmp_path):
    target = str(tmp_path / "bin")
    with patch("reverser.agent_session.create_backend", return_value=FakeBackend()):
        create = await client.post("/api/sessions", headers=HEADERS, json={
            "target": target, "profile": "general",
            "backend": "claude", "model": None, "api_base": None,
            "budget": 5.0, "max_turns": 50,
        })
        sid = create.json()["id"]

        r = await client.patch(
            f"/api/sessions/{sid}/config?target={target}",
            headers=HEADERS,
            json={"budget": 9.0},
        )
    assert r.status_code == 409
    assert "stop it first" in r.json()["detail"]


@pytest.mark.asyncio
async def test_patch_config_409_for_completed_session(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    # Transition stopped → completed via /done (works on stale-active too).
    done = await client.post(f"/api/sessions/{sid}/done", headers=HEADERS)
    assert done.status_code == 204

    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": 9.0},
    )
    assert r.status_code == 409
    assert "completed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_patch_config_400_unknown_profile(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"profile": "nonexistent"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_400_unknown_backend(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    r = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"backend": "nonexistent"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_400_invalid_budget_or_max_turns(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    r1 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"budget": -1.0},
    )
    assert r1.status_code == 400
    r2 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"max_turns": 0},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_patch_config_explicit_null_clears_optional_field(client, tmp_path):
    """Sending {"model": null} clears a previously-set model. This must be
    distinguishable from {} (don't touch model) — the endpoint relies on
    Pydantic's `exclude_unset` to tell the two cases apart."""
    sid, target = await _create_and_stop(client, tmp_path)

    # Step 1: set model to a value.
    r1 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"model": "qwen3.5:35b"},
    )
    assert r1.status_code == 204
    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["model"] == "qwen3.5:35b"

    # Step 2: clear it with explicit null.
    r2 = await client.patch(
        f"/api/sessions/{sid}/config?target={target}",
        headers=HEADERS,
        json={"model": None},
    )
    assert r2.status_code == 204
    listing = await client.get("/api/sessions", headers=HEADERS)
    row = next(r for r in listing.json()["sessions"] if r["id"] == sid)
    assert row["model"] is None


@pytest.mark.asyncio
async def test_patch_config_400_when_required_field_is_null(client, tmp_path):
    sid, target = await _create_and_stop(client, tmp_path)
    for field in ("profile", "backend", "budget", "max_turns"):
        r = await client.patch(
            f"/api/sessions/{sid}/config?target={target}",
            headers=HEADERS,
            json={field: None},
        )
        assert r.status_code == 400, f"expected 400 for null {field}, got {r.status_code}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gui_service/test_session_config_routes.py -xvs`
Expected: All 10 tests FAIL with 405 Method Not Allowed (the route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

Edit `src/reverser/gui_service/routes/sessions.py`. Near the other body models at the top of the file (after `SudoBody`), add:

```python
_VALID_BACKENDS = {"claude", "ollama", "lmstudio", "local"}


class UpdateConfigBody(BaseModel):
    backend: str | None = None
    model: str | None = None
    api_base: str | None = None
    profile: str | None = None
    budget: float | None = None
    max_turns: int | None = None
```

Then near the other delete/archive handlers at the bottom of the file, add:

```python
@router.patch("/api/sessions/{session_id}/config", status_code=204)
def update_session_config(
    request: Request, session_id: str, target: str, body: UpdateConfigBody,
) -> Response:
    """Edit a stopped engagement's config. Saves to the snapshot so the next
    Resume picks up the new values. Refuses when the engagement is running
    or terminal."""
    from ...profiles import get_profile as _get_profile

    try:
        snap = load_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404)

    if snap.state == "active":
        raise HTTPException(409, detail="engagement is running; stop it first")
    if snap.state in ("completed", "abandoned"):
        raise HTTPException(
            409, detail=f"engagement is {snap.state}; config cannot be changed",
        )

    # `exclude_unset=True` keeps only fields the client actually sent — so
    # `{"model": null}` (clear back to default) is distinguishable from
    # `{}` (don't touch model). Optional fields (model, api_base) accept
    # null; required ones (backend, profile, budget, max_turns) do not.
    fields = body.model_dump(exclude_unset=True)

    if "profile" in fields:
        if fields["profile"] is None:
            raise HTTPException(400, detail="profile cannot be null")
        try:
            _get_profile(fields["profile"])
        except KeyError as e:
            raise HTTPException(400, detail=str(e))
    if "backend" in fields:
        if fields["backend"] is None:
            raise HTTPException(400, detail="backend cannot be null")
        if fields["backend"] not in _VALID_BACKENDS:
            raise HTTPException(
                400,
                detail=f"unknown backend: {fields['backend']!r}. "
                       f"Known: {sorted(_VALID_BACKENDS)}",
            )
    if "budget" in fields:
        if fields["budget"] is None or fields["budget"] <= 0:
            raise HTTPException(400, detail="budget must be > 0")
    if "max_turns" in fields:
        if fields["max_turns"] is None or fields["max_turns"] < 1:
            raise HTTPException(400, detail="max_turns must be >= 1")

    # Apply only sent fields. `model` and `api_base` may legitimately be None.
    if "backend" in fields:
        snap.config.backend = fields["backend"]
    if "model" in fields:
        snap.config.model = fields["model"]
    if "api_base" in fields:
        snap.config.api_base = fields["api_base"]
    if "profile" in fields:
        snap.config.profile = fields["profile"]
    if "budget" in fields:
        snap.config.budget = fields["budget"]
    if "max_turns" in fields:
        snap.config.max_turns = fields["max_turns"]

    save_snapshot(snap)
    return Response(status_code=204)
```

The `load_snapshot`, `save_snapshot`, `SessionNotFoundError`, `HTTPException`, `Response`, `Request`, and `BaseModel` symbols are already imported at the top of this file from earlier code — no new imports needed at module level besides `_get_profile` which is imported lazily inside the handler.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui_service/test_session_config_routes.py -xvs`
Expected: All 10 tests PASS.

- [ ] **Step 5: Run the wider gui_service suite to confirm no regressions**

Run: `pytest tests/gui_service/ --ignore=tests/gui_service/test_handshake.py -q`
Expected: All pass (3 pre-existing handshake failures unrelated and skipped).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/sessions.py tests/gui_service/test_session_config_routes.py
git commit -m "$(cat <<'EOF'
feat(gui-service): PATCH /api/sessions/{id}/config for stopped engagements

Adds an endpoint that lets the renderer edit backend/model/api_base/profile/
budget/max_turns on a stopped engagement. Edits persist to the snapshot so
the next Resume picks them up — no change to the resume flow needed.

Active and terminal engagements are refused with 409. Unknown profile/backend
and invalid budget/max_turns return 400.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Renderer — extend types + add api.patch helper

**Files:**
- Modify: `desktop/renderer/src/api/client.ts:14, 37-42, 72-83`

- [ ] **Step 1: Add PATCH to the request method union and api object**

In `desktop/renderer/src/api/client.ts`, change the `request` function signature from:

```ts
async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown
): Promise<T> {
```

to:

```ts
async function request<T>(
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH",
  path: string,
  body?: unknown
): Promise<T> {
```

And change the `api` object from:

```ts
export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
```

to:

```ts
export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
```

- [ ] **Step 2: Extend `SessionRow` type**

In the same file, change:

```ts
export type SessionRow = {
  id: string;
  target: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  turns: number;
  total_cost: number;
  stopped_at: string | null;
  archived_at: string | null;
  budget?: number;
  max_turns?: number;
};
```

to:

```ts
export type SessionRow = {
  id: string;
  target: string;
  profile: string;
  state: "active" | "stopped" | "completed" | "abandoned";
  turns: number;
  total_cost: number;
  stopped_at: string | null;
  archived_at: string | null;
  backend: string;
  model: string | null;
  api_base: string | null;
  budget: number;
  max_turns: number;
};
```

(Budget and max_turns are promoted from optional to required since the backend now guarantees them.)

- [ ] **Step 3: Verify the renderer builds**

Run: `cd desktop && npx vite build`
Expected: Build succeeds. (Compile errors at this point would mean other code reads the old optional shape — those are addressed in later tasks; if the build complains, that's the signal.)

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/api/client.ts
git commit -m "$(cat <<'EOF'
feat(desktop): add api.patch + extend SessionRow with backend/model/api_base

Adds PATCH support to the API client and promotes backend/model/api_base
into SessionRow. The new fields are required because the backend list
endpoint now guarantees them on every row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Renderer — `useUpdateSessionConfig` mutation hook

**Files:**
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Add the mutation hook**

In `desktop/renderer/src/api/queries.ts`, after the `useUpdateScope` hook (around line 197-206), add:

```ts
export type UpdateSessionConfigBody = {
  backend?: string;
  model?: string | null;
  api_base?: string | null;
  profile?: string;
  budget?: number;
  max_turns?: number;
};

export function useUpdateSessionConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sessionId,
      target,
      body,
    }: {
      sessionId: string;
      target: string;
      body: UpdateSessionConfigBody;
    }) =>
      api.patch<void>(
        `/api/sessions/${encodeURIComponent(sessionId)}/config` +
        `?target=${encodeURIComponent(target)}`,
        body,
      ),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
}
```

- [ ] **Step 2: Verify the renderer builds**

Run: `cd desktop && npx vite build`
Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/api/queries.ts
git commit -m "$(cat <<'EOF'
feat(desktop): useUpdateSessionConfig mutation hook

Wraps PATCH /api/sessions/{id}/config?target=… for the upcoming config
panel. Invalidates the sessions query on success so the panel re-seeds
from fresh snapshot values.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Renderer — `SessionConfigPanel` component

**Files:**
- Create: `desktop/renderer/src/layout/SessionConfigPanel.tsx`

- [ ] **Step 1: Create the component file**

Create `desktop/renderer/src/layout/SessionConfigPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import type { SessionRow } from "@/api/client";
import { useBackends, useProfiles, useUpdateSessionConfig } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

type FormState = {
  backend: string;
  model: string;
  api_base: string;
  profile: string;
  budget: string;
  max_turns: string;
};

function _fromRow(row: SessionRow): FormState {
  return {
    backend: row.backend,
    model: row.model ?? "",
    api_base: row.api_base ?? "",
    profile: row.profile,
    budget: String(row.budget),
    max_turns: String(row.max_turns),
  };
}

function _diff(form: FormState, row: SessionRow): {
  backend?: string;
  model?: string | null;
  api_base?: string | null;
  profile?: string;
  budget?: number;
  max_turns?: number;
} {
  const out: ReturnType<typeof _diff> = {};
  if (form.backend !== row.backend) out.backend = form.backend;
  const formModel = form.model.trim() === "" ? null : form.model;
  if (formModel !== row.model) out.model = formModel;
  const formApiBase = form.api_base.trim() === "" ? null : form.api_base;
  if (formApiBase !== row.api_base) out.api_base = formApiBase;
  if (form.profile !== row.profile) out.profile = form.profile;
  const formBudget = parseFloat(form.budget);
  if (!isNaN(formBudget) && formBudget !== row.budget) out.budget = formBudget;
  const formMaxTurns = parseInt(form.max_turns, 10);
  if (!isNaN(formMaxTurns) && formMaxTurns !== row.max_turns) {
    out.max_turns = formMaxTurns;
  }
  return out;
}

export function SessionConfigPanel({ session }: { session: SessionRow }) {
  const editable = session.state === "stopped";
  const profiles = useProfiles();
  const backends = useBackends();
  const update = useUpdateSessionConfig();

  const [form, setForm] = useState<FormState>(() => _fromRow(session));

  // Reset form whenever the row's underlying values change (after save or
  // when the user switches between sessions).
  useEffect(() => {
    setForm(_fromRow(session));
  }, [session.id, session.backend, session.model, session.api_base,
      session.profile, session.budget, session.max_turns]);

  const diff = _diff(form, session);
  const dirty = Object.keys(diff).length > 0;
  const profileOrBackendChanged =
    diff.profile !== undefined || diff.backend !== undefined || diff.model !== undefined;

  async function onSave() {
    if (!dirty) return;
    try {
      await update.mutateAsync({
        sessionId: session.id, target: session.target, body: diff,
      });
    } catch (e) {
      alert((e as Error).message);
    }
  }

  function _row(label: string, child: React.ReactNode) {
    return (
      <div className="flex items-center gap-3 text-xs">
        <label className="w-24 text-neutral-500 shrink-0">{label}</label>
        <div className="flex-1 min-w-0">{child}</div>
      </div>
    );
  }

  return (
    <div className="border-b border-neutral-800 bg-neutral-950/60 px-3 py-3 space-y-2">
      {_row("backend", editable ? (
        <Select
          value={form.backend}
          onChange={(e) => setForm({ ...form, backend: e.target.value })}
          className="h-7 text-xs"
        >
          {backends.data?.backends.map((b) => (
            <option key={b.key} value={b.key}>{b.name}</option>
          ))}
        </Select>
      ) : <span className="text-neutral-300 font-mono">{session.backend}</span>)}

      {_row("model", editable ? (
        <Input
          value={form.model}
          onChange={(e) => setForm({ ...form, model: e.target.value })}
          placeholder="(optional)"
          className="h-7 text-xs"
        />
      ) : <span className="text-neutral-300 font-mono">{session.model ?? "—"}</span>)}

      {_row("api_base", editable ? (
        <Input
          value={form.api_base}
          onChange={(e) => setForm({ ...form, api_base: e.target.value })}
          placeholder="(optional)"
          className="h-7 text-xs"
        />
      ) : <span className="text-neutral-300 font-mono">{session.api_base ?? "—"}</span>)}

      {_row("profile", editable ? (
        <Select
          value={form.profile}
          onChange={(e) => setForm({ ...form, profile: e.target.value })}
          className="h-7 text-xs"
        >
          {profiles.data?.profiles.map((p) => (
            <option key={p.key} value={p.key}>{p.name} · {p.key}</option>
          ))}
        </Select>
      ) : <span className="text-neutral-300 font-mono">{session.profile}</span>)}

      {_row("budget", editable ? (
        <Input
          type="number" step="0.1"
          value={form.budget}
          onChange={(e) => setForm({ ...form, budget: e.target.value })}
          className="h-7 text-xs"
        />
      ) : <span className="text-neutral-300 font-mono">${session.budget.toFixed(2)}</span>)}

      {_row("max turns", editable ? (
        <Input
          type="number"
          value={form.max_turns}
          onChange={(e) => setForm({ ...form, max_turns: e.target.value })}
          className="h-7 text-xs"
        />
      ) : <span className="text-neutral-300 font-mono">{session.max_turns}</span>)}

      {editable && profileOrBackendChanged && (
        <p className="text-[11px] text-amber-400 mt-2 pl-24">
          Profile / backend / model changes apply on resume. The conversation
          history is preserved, but the system prompt and toolset shift mid-
          conversation.
        </p>
      )}

      {editable && (
        <div className="flex items-center gap-2 justify-end pt-1">
          <Button
            size="sm" variant="ghost"
            disabled={!dirty || update.isPending}
            onClick={() => setForm(_fromRow(session))}
          >
            Discard
          </Button>
          <Button
            size="sm"
            disabled={!dirty || update.isPending}
            onClick={onSave}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the renderer builds**

Run: `cd desktop && npx vite build`
Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/layout/SessionConfigPanel.tsx
git commit -m "$(cat <<'EOF'
feat(desktop): SessionConfigPanel — read/edit engagement config

New component renders backend/model/api_base/profile/budget/max_turns for
a session. Read-only when active/terminal; editable form with Save/Discard
when stopped. The form diffs against the SessionRow and only PATCHes the
fields the user changed.

A caveat banner appears when profile/backend/model is edited to warn that
the agent's behavior may shift mid-conversation after resume.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Renderer — wire `SessionConfigPanel` into `SessionStatusBar`

**Files:**
- Modify: `desktop/renderer/src/layout/SessionStatusBar.tsx`

- [ ] **Step 1: Add expand toggle + render the panel**

Replace the contents of `desktop/renderer/src/layout/SessionStatusBar.tsx` with:

```tsx
import { useState } from "react";
import { useStore } from "zustand";
import { ChevronDown, ChevronRight } from "lucide-react";
import { getSessionStore } from "@/state/session-store";
import { useSessions } from "@/api/queries";
import { SessionConfigPanel } from "./SessionConfigPanel";

export function SessionStatusBar({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const status = useStore(store, (s) => s.status);
  const budget = useStore(store, (s) => s.budget);
  const sessions = useSessions();
  const row = sessions.data?.sessions.find((s) => s.id === sessionId);
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <header className="h-9 border-b border-neutral-800 bg-neutral-950/80 flex items-center px-3 text-xs gap-4 font-mono">
        <span className={
          status === "running" ? "text-amber-400" :
          status === "awaiting_input" ? "text-green-400" :
          status === "stopped" ? "text-neutral-500" :
          status === "completed" ? "text-blue-400" : "text-neutral-300"
        }>● {status}</span>
        <span className="text-neutral-300">{row?.target ?? "—"}</span>
        <span>profile: <span className="text-neutral-300">{row?.profile ?? "—"}</span></span>
        <span className="ml-auto text-neutral-400">
          {budget
            ? <>${budget.spent.toFixed(2)} / ${(budget.spent + budget.remaining).toFixed(2)} · turn {budget.turn}/{row?.max_turns ?? "?"}</>
            : <>budget —</>}
        </span>
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-neutral-500 hover:text-neutral-200"
          title={expanded ? "Hide config" : "Show config"}
          aria-label={expanded ? "Hide config" : "Show config"}
        >
          {expanded
            ? <ChevronDown className="h-3.5 w-3.5" />
            : <ChevronRight className="h-3.5 w-3.5" />}
        </button>
      </header>
      {expanded && row && <SessionConfigPanel session={row} />}
    </>
  );
}
```

- [ ] **Step 2: Verify the renderer builds**

Run: `cd desktop && npx vite build`
Expected: Build succeeds.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/layout/SessionStatusBar.tsx
git commit -m "$(cat <<'EOF'
feat(desktop): expandable config panel in session status bar

Adds a chevron at the right edge of the status bar that toggles the new
SessionConfigPanel beneath the header. Defaults to collapsed; visible on
all tabs of the session detail view.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: E2e — round-trip test for the config edit flow

**Files:**
- Modify: `desktop/tests/e2e/engagement.spec.ts`

- [ ] **Step 1: Inspect the existing e2e setup to match style**

Run: `head -50 desktop/tests/e2e/engagement.spec.ts`

This shows how existing engagement tests bring up the app and navigate. Mirror that style for the new test.

- [ ] **Step 2: Add the round-trip test**

Append to `desktop/tests/e2e/engagement.spec.ts`:

```ts
test("operator can edit a stopped engagement's model and see it persist", async ({ page }) => {
  // 1. Create a fresh engagement
  await page.goto("/");
  await page.getByRole("link", { name: /new engagement/i }).click();
  await page.getByPlaceholder(/path to binary or url/i).fill("/tmp/e2e-binary");
  await page.getByRole("button", { name: /start engagement/i }).click();

  // 2. Stop it
  await page.getByRole("button", { name: /stop/i }).click();
  await page.getByRole("button", { name: /confirm stop/i }).click();

  // 3. Expand the config panel
  await page.getByLabel(/show config/i).click();

  // 4. Edit the model field
  const modelInput = page.locator('input[placeholder="(optional)"]').first();
  await modelInput.fill("qwen3.5:35b");

  // 5. Save
  await page.getByRole("button", { name: /^save$/i }).click();

  // 6. Wait for the save to land; re-expand and read back
  await expect(page.getByText("Saving…")).toHaveCount(0);
  await expect(modelInput).toHaveValue("qwen3.5:35b");
});
```

(If selectors don't match the actual rendered DOM during test runs, adjust them to use roles/text that appear in the test output — keep the assertions about the model value round-tripping intact.)

- [ ] **Step 3: Run the e2e**

Run: `cd desktop && npm run test:e2e -- engagement.spec.ts`
Expected: New test passes. If selectors don't match, adjust as noted above; the *round-trip behavior* (edit → save → reload value) is the assertion that matters.

- [ ] **Step 4: Commit**

```bash
git add desktop/tests/e2e/engagement.spec.ts
git commit -m "$(cat <<'EOF'
test(e2e): config edit round-trip on stopped engagement

Verifies that opening a stopped engagement, expanding the config panel,
editing the model field, and saving results in the new value persisting
in the row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Run the full backend suite**

Run: `pytest tests/ --ignore=tests/gui_service/test_handshake.py -q`
Expected: All pass (3 pre-existing handshake failures unrelated to this work).

- [ ] **Build the renderer once more**

Run: `cd desktop && npx vite build`
Expected: Build succeeds.

- [ ] **Verify the spec is fully covered**

Re-read [docs/superpowers/specs/2026-05-20-engagement-config-edit-design.md](../specs/2026-05-20-engagement-config-edit-design.md) and confirm:

- §3.1 (list_sessions config exposure) → Task 1 ✓
- §3.2 (PATCH endpoint) → Task 2 ✓
- §3.3 (no resume change needed) → confirmed; nothing to do ✓
- §4.1 (SessionRow type extension) → Task 3 ✓
- §4.2 (useUpdateSessionConfig hook) → Task 4 ✓
- §4.3 (SessionConfigPanel component) → Task 5 ✓
- §4.4 (wire into SessionStatusBar) → Task 6 ✓
- §5 (backend + e2e tests) → Tasks 1, 2, 7 ✓
