# Engagement Config Display & Edit — Design Spec

**Date:** 2026-05-20
**Status:** Approved for planning
**Scope:** Surface per-engagement runtime config (backend, model, api_base, profile, budget, max_turns) in the session detail view, and allow editing those fields when the engagement is stopped so the next resume can pick them up.

## 1. Goals & non-goals

### Goals

- Operator can see, at a glance, which backend/model/api_base/profile/budget/max_turns each engagement is configured with — without re-deriving from creation flow memory.
- When an engagement is **stopped**, the operator can change any of those fields. Changes commit to the on-disk snapshot. The next Resume uses the updated config.
- When the engagement is **active**, those fields are shown read-only. (Live edits of budget/max_turns via the existing `POST /budget` route remain available; UI wiring for that is a follow-up.)
- When the engagement is **completed** or **abandoned**, fields are read-only — they can no longer be resumed.

### Non-goals (this phase)

- Live editing of backend/model/api_base/profile while the engagement is running — genuinely impossible (the agent process is bound to its backend at construction).
- Live UI for editing budget/max_turns while running — `POST /api/sessions/{id}/budget` exists already; surfacing it can be a follow-up.
- Per-engagement override of `target` — changing the target is logically a new engagement, not an edit.
- Bulk edit across multiple engagements.
- Audit log of past config edits (the snapshot only carries the current values).

## 2. UX

### Display location: collapsible section under the status bar header

The existing `SessionStatusBar` (top of every session detail view) keeps its single-line summary (`status · target · profile · budget`). A chevron (▸ / ▾) is added at the right end of the row. Clicking it expands a panel directly beneath the header — visible regardless of which right-side tab (hypotheses/findings/kb) is active.

The expand state is local to the session view (not persisted) — defaults to collapsed.

### Expanded content — by engagement state

**Active** — read-only:

```
backend:   claude
model:     —
api_base:  —
profile:   ad
budget:    $5.00
max turns: 50
```

Each row is `label: value` in the existing monospace font. Empty optional fields render as `—`.

**Stopped** — editable:

Same fields, each rendered as a labeled input (`Select` for backend/profile, `Input` for everything else). A primary **Save** button at the bottom-right of the panel commits via PATCH; a **Discard** button next to it reverts the form to the snapshot's current values. The Save button is disabled if no field has been edited.

The existing "Resume engagement" button stays in its existing banner above the chat pane; clicking it resumes with whatever's currently saved on the snapshot. (The two actions are intentionally separate — "edit config" and "resume" are independent operations.)

When the user edits `profile`, `backend`, or `model`, a small caveat appears under the form:

> *Profile / backend / model changes apply on resume. The conversation history is preserved, but the system prompt and toolset shift mid-conversation.*

**Completed / abandoned** — read-only (same layout as Active).

### Single source of truth

The form's initial state and the read-only display both read from the same fields on the `SessionRow`. After PATCH succeeds, the `sessions` query is invalidated and the form re-seeds from the new snapshot values.

## 3. Backend changes

### 3.1 Expose config fields in `list_sessions`

`SessionManager.list_sessions` in [session_manager.py:152](../../../src/reverser/gui_service/session_manager.py:152) currently emits per-row:

```python
{
  "id", "target", "profile", "state",
  "turns", "total_cost", "stopped_at", "archived_at"
}
```

Extend to also include `backend`, `model`, `api_base`, `budget`, `max_turns` (the latter two already optionally appear for active rows via `_serialize`; make them present on every row).

`_serialize` (used when the active session isn't on disk yet) gets the same extension.

The route `GET /api/sessions` returns the same shape — no route change beyond the dict-shape extension.

### 3.2 New endpoint: `PATCH /api/sessions/{session_id}/config`

Query param: `target` (required — same convention as the other per-session routes).

Body (all fields optional; only sent fields are applied):

```json
{
  "backend": "claude",
  "model": "qwen3.5:35b",
  "api_base": "http://localhost:11434/v1",
  "profile": "ad",
  "budget": 7.5,
  "max_turns": 75
}
```

Returns:
- **204** on success.
- **400** if a field fails validation (unknown profile, unknown backend, negative budget, max_turns < 1).
- **404** if the snapshot doesn't exist.
- **409** if the snapshot's state is `active`, `completed`, or `abandoned`. Body message identifies which:
  - active: "engagement is running; stop it first"
  - terminal: "engagement is {state}; config cannot be changed"

Implementation:
1. Load snapshot via `sessions.load(target, session_id)`.
2. Reject by state per above.
3. Validate each provided field: `profile` exists in `reverser.profiles.PROFILES`; `backend` exists in the backend registry; `budget > 0`; `max_turns >= 1`. The handler raises `HTTPException(400)` on first failure.
4. Mutate `snap.config.{field}` for each provided field.
5. `sessions.save(snap)`.
6. Return 204.

The endpoint lives in [routes/sessions.py](../../../src/reverser/gui_service/routes/sessions.py) alongside the other per-session routes. A new pydantic body model `UpdateConfigBody` mirrors the JSON shape with all-optional fields.

### 3.3 No change to resume

Because edits commit to the snapshot before resume, `SessionManager.resume_session` reads them naturally via `snap.config.*`. The profile-match check in `AgentSession._init_resumed`:

```python
if snap.config.profile != profile.key:
    raise ValueError(...)
```

…still passes — the manager reads `snap.config.profile` and looks up the same profile, so the two always match.

No change to the existing `POST /api/sessions/{id}/budget` endpoint either; it remains the live-edit path for active sessions.

## 4. Frontend changes

### 4.1 Types

`SessionRow` in [api/client.ts:72](../../../desktop/renderer/src/api/client.ts:72) extends to include `backend: string`, `model: string | null`, `api_base: string | null`. Budget and max_turns are already optional fields; promote to required since 3.1 guarantees them.

### 4.2 New query hook

`useUpdateSessionConfig` in [api/queries.ts](../../../desktop/renderer/src/api/queries.ts):

```ts
useMutation({
  mutationFn: ({ sessionId, target, body }) =>
    api.patch(`/api/sessions/${sessionId}/config?target=${target}`, body),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
})
```

`api.patch` doesn't exist yet; add it as a sibling to `api.post`/`api.del`.

### 4.3 New component: `SessionConfigPanel`

New file `desktop/renderer/src/layout/SessionConfigPanel.tsx`. Takes a `SessionRow` and an `expanded: boolean` prop. Renders:

- The six rows of config (backend, model, api_base, profile, budget, max_turns).
- In `stopped` state: each row is an editable input bound to local form state. Save/Discard buttons at bottom.
- In all other states: read-only spans.

The form uses controlled inputs seeded from the `SessionRow` props. A local `dirty` flag enables/disables Save. On save success, the form re-syncs from the refetched row.

### 4.4 Wire into SessionStatusBar

`SessionStatusBar` gains:
- A `useState<boolean>(false)` for `expanded`.
- A chevron button at the right end of the header (next to the budget summary), toggling `expanded`.
- Renders `<SessionConfigPanel session={row} expanded={expanded} />` directly beneath itself when expanded.

Profiles and backends lists for the selects come from the existing `useProfiles()` and `useBackends()` hooks.

## 5. Testing

### Backend

In `tests/gui_service/test_sessions_routes.py` (or a new `test_session_config_routes.py`):

- `PATCH /config` on a stopped session updates the snapshot; subsequent `GET /sessions` reflects the change.
- `PATCH` with only a subset of fields leaves the others unchanged.
- `PATCH` on an active session returns 409 with the "stop it first" message.
- `PATCH` on a completed session returns 409 with the "terminal" message.
- `PATCH` with `profile: "nonexistent"` returns 400.
- `PATCH` with `backend: "nonexistent"` returns 400.
- `PATCH` with `budget: -1` returns 400.
- `PATCH` with `max_turns: 0` returns 400.
- `PATCH` on missing snapshot returns 404.

In `tests/gui_service/test_session_manager.py`:

- `list_sessions()` includes `backend`, `model`, `api_base`, `budget`, `max_turns` on every row (both active and disk-only).

In `tests/gui_service/test_session_lifecycle.py` (or a new e2e test):

- Create session → stop → PATCH config (change backend & model) → resume → the resumed session's stats / snapshot reflect the new config.

### Frontend

E2e via Playwright in `desktop/tests/e2e/engagement.spec.ts` (or extend an existing spec):

- Open a stopped engagement → expand config panel → edit model → save → row in sidebar reflects new model.
- Open an active engagement → expand config panel → inputs are read-only / replaced with spans.

(No unit-test harness for React components — covered by e2e.)

## 6. Edge cases & decisions

| Case | Decision |
|---|---|
| User edits config, doesn't save, navigates away | Edits are lost (local form state only). No "unsaved changes" warning — matches the rest of the app. |
| User edits and saves, then resumes — backend rejects (e.g. ollama unreachable) | Standard resume error path. The snapshot keeps the new (now-invalid) config; user can edit again and re-resume. |
| Snapshot has an empty `model: null` for a backend that requires one | The select renders an empty option; save fails validation if backend requires it (validation happens server-side at PATCH time). |
| Two clients editing the same stopped session | Last writer wins. No optimistic locking. |
| Profile change leaves in-flight dispatch state stale | `snap.in_flight` is cleared on `stop()` already, so this is moot. |

## 7. Risks

- **Profile/backend change mid-conversation produces strange agent behavior.** The conversation history was generated with a different system prompt and toolset. Mitigation: the caveat banner in §2. We don't prevent it because there are legitimate reasons (e.g., switching from "general" recon to "ad" after initial discovery).
- **Hand-edited snapshots could already have unusual config values.** The PATCH validation runs on every save, so going forward all server-persisted edits pass validation. Pre-existing weirdness is unchanged.

## 8. Implementation order

1. Backend: extend `list_sessions` / `_serialize` (3.1) + tests.
2. Backend: PATCH endpoint (3.2) + tests.
3. Frontend: extend types + add `api.patch` + `useUpdateSessionConfig` hook.
4. Frontend: `SessionConfigPanel` component (read-only mode first).
5. Frontend: wire into `SessionStatusBar` with expand/collapse.
6. Frontend: editable form mode for stopped state.
7. E2e test for the round-trip.
