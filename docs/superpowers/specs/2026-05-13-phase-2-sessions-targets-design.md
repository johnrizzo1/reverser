# Phase 2 — Sessions & Multi-Engagement UX — Design Spec

**Date:** 2026-05-13
**Status:** Approved for planning
**Scope:** Cross-target sessions browser, per-target dashboard, and read-only session detail. Builds on the Phase 0 + Phase 1 desktop UI ([2026-05-13-electron-desktop-ui-design.md](2026-05-13-electron-desktop-ui-design.md)).

## 1. Goals & non-goals

### Goals

- Let the operator browse and switch between *any* session across all targets, without needing to know the target up front.
- Provide a per-target dashboard summarizing cumulative state (sessions count, total cost, latest activity, KB rollups).
- Make stopped, completed, and abandoned sessions browsable in the same UI as live ones — read-only — so post-engagement review happens inside the app instead of in `targets/<t>/sessions/<id>.json`.
- Preserve the Phase 1 invariant: at most one *active* session at a time. Browsing a non-active session does not start an agent loop.

### Non-goals (this phase)

- True parallel multi-session execution (still one agent loop at a time; the UI surfaces remain ready).
- Full event-stream replay for completed sessions (recorded tool-call timeline). Phase 3.
- Cross-session search across chat content. Phase 3.
- Pinning / favorite-ing sessions or targets. Phase 4 polish.
- BloodHound graph, evidence gallery, scope.toml editor. Phase 3.

## 2. Architecture

Phase 2 is frontend-heavy. Backend adds two read-only endpoints; the rest is new React components reusing existing data sources.

```
┌────┐
│ 🏠 │  /                Dashboard
│ 📋 │  /sessions[/:id]  SessionsPanel + SessionLayout
│ 🎯 │  /targets         TargetsPanel
│    │  /target/:name    TargetOverview
│ ❤️ │  /health          Service health
│ ⚙️ │  /settings        (Phase 4 placeholder)
└────┘
```

Two new activity-bar icons (Sessions, Targets). Sessions and Targets each have a side-panel component that stays mounted while you navigate to a child route (so you can hop between sessions / targets without backtracking). Both panels live inside the existing `Shell` layout — no new top-level chrome.

The existing `/session/:id` route currently bypasses `Shell` (full-screen IDE chrome). Phase 2 brings it back inside `Shell` so the SessionsPanel stays visible while you're in a session. The IDE multi-pane chrome stays — the activity bar + side panel just become a thin left strip.

## 3. Routes

| Route | Side panel | Main content |
|---|---|---|
| `/` | (none) | Dashboard (profile grid, CTA, recent sessions stays here for now) |
| `/sessions` | `SessionsPanel` | "Select a session" placeholder |
| `/sessions/:id` | `SessionsPanel` (row highlighted) | `SessionLayout` (active or read-only mode) |
| `/targets` | `TargetsPanel` | "Select a target" placeholder |
| `/target/:name` | `TargetsPanel` (row highlighted) | `TargetOverview` |
| `/new` | (none) | `NewEngagement` wizard (unchanged) |
| `/health`, `/settings` | (none) | unchanged |

`/session/:id` (the Phase 1 route) is kept as a redirect to `/sessions/:id` so existing in-flight bookmarks/tests still work.

## 4. SessionsPanel

Side-panel component, ~240 px wide.

**Structure:**

```
┌─ SESSIONS ─────────────────┐
│ [all] [active] [stopped]   │  filter tabs
│ [done] [abandoned]          │
│ ┌────────────────────────┐ │
│ │ filter target/profile… │ │  substring search
│ └────────────────────────┘ │
│ ─────────────────────────  │
│ ● 10.10.10.5               │
│   manager · 11:04 · $0.42  │
│ ─────────────────────────  │
│ ⏸ app.example.com          │
│   webpentest · 22:54·$2.11 │
└─────────────────────────────┘
```

**Per-row fields:** state dot (green = active, amber = stopped, blue = completed, gray = abandoned), target, profile, `last_active_at` (formatted), `total_cost`. Click → navigate to `/sessions/:id`.

**Sort:** by `last_active_at` desc (most recently touched first). Active sessions always sort first regardless of timestamp (rare but principled — you want to see the running one).

**Filter tabs:** map directly to `SessionRow.state`. `[all]` is the default. Each tab shows a count: `active (1) · stopped (3) · …`.

**Search:** case-insensitive substring match against `target`, `profile`, and `id` (session id). 100 ms debounce.

**Data source:** existing `GET /api/sessions`, polled every 5 s (matches current TanStack Query config). No new endpoint.

## 5. TargetsPanel

Side-panel component, ~240 px wide.

**Structure:**

```
┌─ TARGETS ──────────────────┐
│ [by activity] [by name]    │  sort toggle
│ ┌────────────────────────┐ │
│ │ filter…                │ │
│ └────────────────────────┘ │
│ ─────────────────────────  │
│ 10.10.10.5                 │
│   3 sessions · $1.42       │
│   ● active · 2 findings    │
│ ─────────────────────────  │
│ app.example.com            │
│   5 sessions · $4.87       │
│   ⏸ 22:54 · 9 findings     │
└─────────────────────────────┘
```

**Per-row fields:** target name, session count, total cost across all sessions, latest state (with state dot), finding count. Click → `/target/:name`.

**Sort:** by `last_activity` desc (default), toggleable to alphabetical by target name.

**Search:** substring match against target name. 100 ms debounce.

**Data source:** existing `GET /api/targets` for the list of target names (and `has_kb` / `has_scope` flags). Each row's stats come from `GET /api/targets/{name}/summary` (new — see §7). The panel fires N parallel summary fetches; TanStack Query dedupes / caches per name.

Alternative considered and rejected: include the summary inline in `GET /api/targets`. Rejected because that endpoint is currently a cheap directory scan; adding summary rollup would slow it down even when the renderer just wants the names (e.g., the wizard's target dropdown — Phase 4).

## 6. `/target/:name` — TargetOverview

Main-content page. The TargetsPanel side-panel stays mounted.

**Structure:**

```
┌─ TargetOverview for 10.10.10.5 ──────────────────────────┐
│                                                          │
│ ┌─ Summary ────────────────────────────────────────────┐ │
│ │ profile most used: manager      total spend: $1.42   │ │
│ │ first activity: 2026-05-09  last: 2026-05-13         │ │
│ │ 3 sessions · 12 hosts · 2 creds · 5 findings · 3 hyps│ │
│ │                                       [New engagement]│ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌─ Sessions ───────────┐  ┌─ KB ────────────────────────┐│
│ │ ● active             │  │ [Findings] [Hyps] [Hosts]   ││
│ │   manager · 11:04    │  │ [Services] [Creds]          ││
│ │ ⏸ stopped            │  │ ───────────────             ││
│ │   manager · 09:15    │  │ ● high reflected XSS        ││
│ │ ✓ completed          │  │ ● med outdated nginx        ││
│ │   ad · 05-11 18:22   │  │ ● info wp-version 6.4.1     ││
│ └──────────────────────┘  └─────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
```

**Three regions:**

1. **Summary card (top, full-width):** pulls `GET /api/targets/{name}/summary`. The "New engagement" CTA pre-fills the wizard's target field.
2. **Sessions for this target (left half):** reuses the SessionsPanel row component, filtered client-side from `useSessions()` where `s.target === name`. Click → `/sessions/:id`.
3. **KB snapshot (right half, tabbed):** five tabs — Findings · Hypotheses · Hosts · Services · Creds. Each renders a compact table from `GET /api/targets/{name}/kb`. Findings sorted by severity (critical → info); Hosts and Services as simple tables; Creds with validation status column.

**No live updates required.** Data refreshes on mount / window focus. The live view is `/sessions/:id`.

## 7. Backend additions

### `GET /api/targets/{name}/summary`

Rolled-up state for the TargetOverview summary card and TargetsPanel rows. Single endpoint avoids the renderer fanning out across N sessions + the full KB.

Response:

```jsonc
{
  "target": "10.10.10.5",
  "sessions": {
    "total": 3,
    "by_state": { "active": 1, "stopped": 1, "completed": 1, "abandoned": 0 }
  },
  "spend": { "total_usd": 1.42 },
  "profiles_used": ["manager", "ad"],   // distinct, by use-frequency desc
  "first_activity": "2026-05-09T18:22:00Z",
  "last_activity": "2026-05-13T11:04:00Z",
  "kb_counts": {
    "hosts": 12, "services": 28, "credentials": 2,
    "findings": 5, "hypotheses": 3, "artifacts": 4, "notes": 1
  }
}
```

**Implementation:** iterate `sessions.list_all()` filtering by target; aggregate; then call `kb.for_target(name).count_*()` (or `len(list_*())` fallback). Returns 404 if no `targets/{name}/` dir exists.

### `GET /api/sessions/conversation/{id}?target={name}`

Snapshot conversation history for replaying chat in read-only mode. Required because `sessions.load(target, session_id)` takes both args (Phase 1 / Plan 3a discovery).

Response:

```jsonc
{
  "id": "2026-05-12T22-54-46",
  "target": "app.example.com",
  "profile": "webpentest",
  "state": "stopped",
  "conversation": [
    {
      "user": "look at the login form",
      "agent": "Found 3 input fields…",
      "turn": 1,
      "timestamp": "2026-05-12T22:55:14Z",
      "cost": 0.12
    }
  ]
}
```

Returns 404 if the snapshot doesn't exist. Reads only the snapshot file (does not load a live session).

Both endpoints sit behind the existing `require_token` dependency. No new auth model.

## 8. Read-only mode for `/sessions/:id`

`SessionLayout` is the existing component. Phase 2 adds a single derived value:

```ts
const isActive = row?.state === "active";
```

The view differs in five places:

1. **Status pill color:** gray when not active (was always amber-or-green).
2. **Resume banner** (stopped only): one-line message + "Resume engagement" button. Button calls `useResumeSession` → navigates to the new active session's id. Not shown for completed / abandoned states (terminal).
3. **Chat input, Send button, F-key footer:** hidden entirely (not disabled). Replaced by a thin "view-only mode" hint bar with the session's id and timestamp.
4. **Action bar buttons** (Skill / Sudo / Stop / Done): removed.
5. **All panes (chat, tool timeline, KB, findings, hypotheses):** unchanged — readable history is the whole point.

**WebSocket:** `useSessionStream(sessionId)` is gated on `isActive`. No WS opened for read-only views.

**History seed:** when opening a non-active session, fetch `GET /api/sessions/conversation/{id}?target={t}` once on mount and seed the store's `messages` array. Phase 2 does not replay the tool-call timeline (the snapshot doesn't preserve it); the timeline pane shows "no recorded tool calls for this session" for read-only views. KB / findings / hypotheses come from `GET /api/targets/{t}/kb` polling (unchanged).

## 9. Frontend file layout

New files:

```
desktop/renderer/src/
  pages/
    SessionsIndex.tsx        — "select a session" placeholder, /sessions
    TargetsIndex.tsx         — "select a target" placeholder, /targets
    TargetOverview.tsx       — /target/:name page
  layout/
    SessionsPanel.tsx        — side-panel sessions list with filters
    TargetsPanel.tsx         — side-panel targets list
  components/
    SessionRow.tsx           — shared row component (used by SessionsPanel
                               + Sessions section of TargetOverview)
    KBTabbedView.tsx         — Findings/Hyps/Hosts/Services/Creds tabs
                               (used by TargetOverview right half)
```

Modified files:

```
desktop/renderer/src/
  App.tsx                    — new routes; move /session/:id under Shell
                               (with redirect from old /session/:id)
  layout/ActivityBar.tsx     — add Sessions + Targets icons
  layout/SessionLayout.tsx   — derive isActive, conditional rendering
                               (resume banner, input bar, action buttons)
  pages/Dashboard.tsx        — keep profile grid + CTA; remove "Recent
                               sessions" card (moved to SessionsPanel)
  api/client.ts              — add TargetSummary + ConversationResponse types
  api/queries.ts             — add useTargetSummary, useConversation hooks
  state/session-store.ts     — add seedConversation(exchanges[]) action
```

Backend:

```
src/reverser/gui_service/
  routes/targets.py          — add /api/targets/{name}/summary handler
  routes/sessions.py         — add /api/sessions/conversation/{id} handler
tests/gui_service/
  test_targets_summary.py    — new
  test_conversation.py       — new
```

## 10. Testing strategy

**Backend (pytest):**

- `test_targets_summary.py`:
  - empty target dir (no sessions, no KB) → all counts zero
  - target with mixed-state sessions → `by_state` counts correct, `total_usd` sums match
  - target with KB populated → `kb_counts` match the existing list endpoints
  - 404 when target dir is absent
- `test_conversation.py`:
  - existing snapshot → conversation array round-trips correctly
  - missing snapshot → 404
  - missing `?target=` query → 400 (FastAPI body validation)

**Frontend (vitest):**

- `SessionRow` renders correct state dot for each `state` value
- `SessionsPanel` filter tabs filter correctly
- `SessionsPanel` search filters correctly across `target` + `profile` + `id`
- `TargetsPanel` sort toggle reorders correctly
- `SessionLayout` in read-only mode hides input + action bar, shows resume banner only for stopped

**Frontend (Playwright e2e):**

- Navigate Dashboard → click 📋 → `/sessions` renders panel with placeholder
- Click a session in the panel → loads `/sessions/:id` with the SessionLayout
- Open a stopped session → input is gone, Resume banner is visible
- Navigate Dashboard → click 🎯 → `/targets` renders
- Click a target → `/target/:name` shows summary card + sessions + KB tabs

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `GET /api/targets/{name}/summary` becomes slow if KB grows large. | Phase 2 uses `len(list_*())` for counts. If profiling shows a problem, add a `count_*()` family to the KB store (cheap SQL `SELECT COUNT(*)`). Spec-level fallback documented. |
| TargetsPanel fans out N summary fetches on mount (one per target). | TanStack Query dedupes + caches with `staleTime: 30_000`. For 5-10 targets this is cheap. Past 50, batch-summary endpoint becomes worth it — defer to Phase 4. |
| `SessionLayout` getting more conditional logic makes it harder to follow. | Split `SessionLayout` into `SessionLayoutActive` and `SessionLayoutReadOnly` only if it grows past ~250 lines. For now, the conditionals are small and localized. |
| Old `/session/:id` bookmarks break. | Add a redirect route in App.tsx; existing Playwright e2e tests update to the new path. |
| Read-only mode might "feel broken" because tool timeline is empty. | Empty-state copy: "no recorded tool calls for this session — Phase 3 will replay from the session log." |

## 12. Out of scope (for future phases)

- Event-log replay (full tool-call timeline for completed sessions) — Phase 3.
- Cross-session full-text search of chat content — Phase 3.
- BloodHound graph view, evidence gallery, scope.toml editor — Phase 3.
- Per-target cost limits, batch-summary endpoint — Phase 4.
- Pin / favorite sessions or targets — Phase 4.

## 13. Open questions

None blocking.
