# Electron Desktop UI — Design Spec

**Date:** 2026-05-13
**Status:** Approved for planning
**Scope:** A feature-complete desktop application that exposes the full reverser engagement lifecycle (configure, execute, review, switch) alongside the existing Textual TUI. The TUI is not replaced.

## 1. Goals & non-goals

### Goals

- Provide a richer interface than the TUI for the full engagement lifecycle: new-engagement configuration, live execution, mid-engagement review, post-engagement review, multi-engagement switching.
- Surface things the TUI literally cannot show: hypothesis trees as visual trees, BloodHound attack-path graphs, screenshot evidence galleries, multi-pane situational awareness.
- Share a single source of truth with the TUI. Both front-ends sit on top of the existing `AgentSession`, profile registry, KB, and session-snapshot code. No duplicated agent or tool logic.
- Preserve every existing safety property: pentest authorization gate, scope envelope enforcement, connection circuit breaker, K-failure pivot rule, snapshot-on-exit, sandbox boundaries.

### Non-goals (v1)

- Remote / multi-user access. The service binds 127.0.0.1.
- Running multiple engagements actively in parallel (one agent loop at a time; UI shell is ready for the upgrade later).
- Bundling Python + native deps into a single installer. `devenv shell` remains the supported way to run the backend; the Electron app is the UI on top.
- Auto-update / signed installer infrastructure. Deferred to Phase 4 at earliest.
- Replacing the TUI. Both front-ends continue to be supported.

## 2. Architecture

Three processes, owned by Electron's main process.

```
┌─────────────────────────────────────────────────────────────────┐
│ Electron (Node)                                                 │
│                                                                 │
│  ┌──────────────────┐         ┌─────────────────────────────┐   │
│  │ Main process     │ spawns  │ Python child (FastAPI)      │   │
│  │                  ├────────►│ python -m reverser          │   │
│  │ - supervise      │         │   gui_service               │   │
│  │ - handshake      │         │                             │   │
│  │   {port, token}  │         │ binds 127.0.0.1:<random>    │   │
│  │ - kill on quit   │         │ exposes /api/* + /ws/*      │   │
│  │ - file dialogs   │         │ wraps AgentSession, KB,     │   │
│  │ - safeStorage    │         │ profiles, sessions          │   │
│  └────────┬─────────┘         └──────────────▲──────────────┘   │
│           │ contextBridge                    │ fetch + WS        │
│           ▼                                  │ (token in header) │
│  ┌──────────────────────────────────────────┴──────────────┐    │
│  │ Renderer (React + TS + Vite, sandboxed)                 │    │
│  │  Pages: Dashboard · NewEngagement · Session · Settings  │    │
│  │  Panes: Chat · ToolTimeline · KB · Hypotheses ·         │    │
│  │         Findings · Evidence · Scope · Health            │    │
│  │  State: TanStack Query (REST) + useSessionStream (WS)   │    │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼  (unchanged)
                          targets/<target>/{state.db, sessions/, findings/, …}
                          logs/<binary>_<timestamp>.jsonl
```

### Boundaries

- **Main process (Node):** OS-level concerns — window lifecycle, Python child supervision, native file dialogs, OS keychain via `safeStorage`, opening external URLs/files.
- **Python child (FastAPI):** A new thin layer at `src/reverser/gui_service/` that wraps the existing `AgentSession`, profile registry, KB, and session-snapshot code. No agent or tool logic is re-implemented.
- **Renderer (React):** Sandboxed (`nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`). All agent control flows through HTTP+WS to localhost. Privileged operations come through a narrow `contextBridge` surface: open-file dialog, open-external, get-app-version.

### Why HTTP+WebSocket (over stdio JSON-RPC)

- Native fit for fan-out: one `AgentEvent` stream → many panes (chat, tool timeline, KB, hypotheses, findings, evidence) listening to the same WebSocket.
- Trivially debuggable with `curl`, `wscat`, browser devtools network panel.
- Multi-session parallelism (Phase ≥ 2) is a small step away — already multiplexed by `session_id`.
- The same backend can serve a pure web UI later if useful, no fork.

## 3. Python service surface

Module: `src/reverser/gui_service/`. Public entrypoint: `python -m reverser.gui_service`.

### Startup handshake

On launch the service writes one JSON line to stdout, then keeps stdout for logs:

```json
{"type":"ready","port":52341,"token":"a8c4...","pid":12345,"version":"…"}
```

Electron's main reads this line and uses `http://127.0.0.1:<port>` with `Authorization: Bearer <token>` on every request and `?token=…` on WS upgrade. Token is 32 random bytes per launch.

### REST endpoints

```
GET    /api/profiles                       → list of profiles + skills + tool surfaces
GET    /api/backends                       → claude/ollama/lmstudio status + model lists
GET    /api/health                         → MSF rpcd / Neo4j / Playwright Chromium presence

GET    /api/targets                        → known targets (from targets/ dir scan)
GET    /api/targets/{target}/kb            → hosts, services, creds, findings, hypotheses, artifacts
GET    /api/targets/{target}/findings/{id}/screenshots/{n} → image bytes
GET    /api/targets/{target}/scope         → scope.toml as parsed JSON
PUT    /api/targets/{target}/scope         → save scope.toml

GET    /api/sessions                       → all sessions across all targets (active/stopped/completed/abandoned)
POST   /api/sessions                       → create a new engagement
                                              {target, profile, backend, model, budget, max_turns}
GET    /api/sessions/{id}                  → snapshot + stats
POST   /api/sessions/{id}/resume           → mark as resumed (idempotent)
POST   /api/sessions/{id}/stop             → graceful stop (writes snapshot)
POST   /api/sessions/{id}/done             → mark completed
POST   /api/sessions/{id}/messages         → {text} — sends user input
POST   /api/sessions/{id}/skills/{key}     → trigger a skill (F1-equivalent)
POST   /api/sessions/{id}/budget           → {budget|max_turns} — preserves conversation
POST   /api/sessions/{id}/sudo             → opaque value, in-memory only

GET    /api/logs/{path}                    → tail or full read of session log
POST   /api/settings/keys                  → write API key to OS keychain (proxied to main)
```

### WebSocket: `/ws/sessions/{id}`

One socket per open session. Server pushes one JSON message per `AgentEvent`. Client demultiplexes by `type` and fans events out to interested panes.

Event taxonomy (one per WS frame):

```jsonc
{"type":"text",           "role":"assistant", "delta":"…"}
{"type":"tool_call",      "id":"t_42", "name":"nmap_scan", "args":{...}}
{"type":"tool_result",    "id":"t_42", "ok":true, "preview":"…"}
{"type":"thinking",       "delta":"…", "redacted":false}
{"type":"kb_update",      "kind":"host"|"service"|"cred"|"finding"|"hypothesis"|"artifact", "row":{...}}
{"type":"hypothesis",     "action":"add"|"update"|"refute"|"confirm", "row":{...}}
{"type":"finding",        "row":{...}}
{"type":"dispatch",       "specialist":"webpentest", "child_session_id":"…",
                          "phase":"start"|"result"|"error"}
{"type":"budget",         "spent":0.42, "remaining":4.58, "turn":7}
{"type":"conn_breaker",   "target":"10.10.10.5", "tripped":true}
{"type":"status",         "phase":"running"|"awaiting_input"|"stopped"|"completed"|"error"}
{"type":"log",            "level":"info"|"warn"|"error", "msg":"…"}
```

Client → server frames are accepted on the same socket for backpressure-sensitive things only:

```jsonc
{"type":"pause"}
{"type":"abort_tool", "id":"t_42"}
```

Everything else (sending messages, triggering skills) goes through REST for easier debuggability.

### Wrapping the existing core

A new `GUISession` adapter in `gui_service/session_adapter.py` constructs an `AgentSession` (the same one the TUI uses), subscribes to its event callbacks, and converts them to WS frames. **No fork of `agent.py`, `prompts.py`, or `profiles/`.**

One small refactor is needed: today `AgentSession`'s event callbacks emit Textual-flavored data. They are split into:
- A backend-neutral `AgentEvent` emission layer (source of truth, lives in `AgentSession`).
- TUI adapter (existing code, moved without behavior change).
- GUI WS adapter (new).

The TUI continues to work unchanged after this split.

## 4. Renderer architecture

### Layout — IDE-style (VS Code-like)

- **Top status bar:** target, profile, backend, budget remaining, turn / max-turns, connection indicator.
- **Activity bar (far left, 48 px):** icons for Sessions, KB, Hypotheses, Findings, Evidence, Scope, Health, Settings. Clicking an icon toggles its side panel.
- **Side panel (left, resizable, hideable):** content depends on active activity-bar icon. Sessions list is the default.
- **Center pane (chat):** the primary view. Always visible. Streams assistant text, dispatches, user messages, status updates.
- **Right rail (tabbed, resizable):** Hypotheses · Findings · KB tabs. Live-updated from the WS stream.
- **Bottom panel (collapsible):** Tool timeline · Log · Evidence tabs. Tool timeline default.
- **Footer:** F-key hints (F1 skill picker, F2 profile switch, F6 stop, etc.) to preserve TUI muscle memory.

### Pages

- `/` — **Dashboard.** Sessions across all targets (filterable). Per-target summary tiles. Recently active engagements at top. "New engagement" CTA.
- `/new` — **New engagement wizard.** Target + profile picker (with description, tool list, K-pivot rule) + backend + model + budget + max-turns + sudo (optional) + scope.toml preview/edit. Submit → POST `/api/sessions` → redirect to `/session/{id}`.
- `/session/:id` — **Live command center.** The IDE-style layout above. The same page serves stopped/completed engagements (read-only, no input).
- `/settings` — **Backends, API keys, health, target hygiene.**

### State management

- **TanStack Query** for REST: `useProfiles`, `useSessions`, `useTargetKB(target)`, `useFindings(target)`, etc. Standard cache + retry behavior.
- **`useSessionStream(sessionId)`** — a custom hook that opens one WebSocket per session, parses frames, dispatches into a per-session **Zustand store**. Panes subscribe via typed selectors (`useChatMessages(sessionId)`, `useToolCalls(sessionId)`, `useHypotheses(sessionId)`, …). One socket → many panes, zero N+1.
- Mutations (`sendMessage`, `triggerSkill`, `setBudget`) are TanStack Query mutations that invalidate relevant queries on success.

### Frontend stack

- Electron 32+ (current LTS at time of build).
- Vite + React 18 + TypeScript.
- Tailwind CSS + shadcn/ui (well-trodden; matches the skill ecosystem's defaults).
- TanStack Query for REST state.
- Zustand for the WS event store.
- `react-resizable-panels` for IDE-style splits.
- Monaco (read-only) for JSON / raw tool output.
- `react-arborist` for the hypothesis tree (Phase 3).
- Cytoscape.js for the eventual BloodHound graph view (Phase 3, lazy-loaded).
- Lucide for icons.

### Visual direction

Dark-first theme, neutral grays with accent colors for severity (red high, amber medium, neutral info) and status (green confirmed, amber testing, gray proposed/refuted). Final palette and component polish handled during Phase 1 implementation; not a load-bearing design decision here.

## 5. Lifecycle, supervision, and security

### Python child lifecycle (owned by Electron main)

- **Launch:** spawn `python -m reverser.gui_service` with `cwd` at the project root and the user's `devenv shell` env vars inherited. Read one JSON line from stdout for `{port, token, pid}`. Subsequent stdout/stderr lines are forwarded to a viewable in-app log buffer (last 200 lines kept).
- **Unexpected exit:** show a recoverable error banner ("backend died — restart?") rather than killing the window. Last 200 stderr lines remain available for diagnosis.
- **App quit:** SIGTERM to Python, wait 5 s, then SIGKILL. The existing `atexit` + SIGTERM handler in `sessions.py` already snapshots in-flight sessions on the Python side — that path keeps working.
- **Sudo password and per-session secrets:** in-memory on the Python side only (never written to disk, never logged). On Python child restart they need to be re-entered (same as TUI behavior today).

### Auth & secrets

- **Per-launch token:** 32-byte random hex. Sent as `Authorization: Bearer <t>` on REST, `?token=…` on WS upgrade. Service rejects everything else with 401. Token never leaves the machine.
- **API keys:** stored in the OS keychain via Electron `safeStorage`. Service requests them from main process over IPC at session-start. Never written to plain files, never present in renderer state.
- **Renderer sandbox:** `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`. `contextBridge` exposes only: open-file dialog (binary uploads), open-external (open report files), get-app-version. All other privileged work goes through HTTP+WS.

### Authorization gate (carries over from CLI/TUI)

Network-touching engagements still require `REVERSER_PENTEST_AUTHORIZED=1` or `.reverser-authorized` in the project root. The service refuses to create a network-target session otherwise. The renderer surfaces this as a one-time confirmation modal that writes `.reverser-authorized` if the user explicitly accepts the language ("I have written authorization to test this target").

### Scope envelope

`scope.toml` enforcement happens on the existing tool side and is unchanged. The renderer offers a form-based editor in Phase 3 for the scope envelope structure (in_scope_cidrs, no_dos, no_account_lockout, allowed_hours).

## 6. Phasing

Five phases, each independently shippable.

| Phase | Scope | Output |
|---|---|---|
| **0 — Foundation** | Electron + Vite + React scaffold. `reverser.gui_service` skeleton: handshake, auth token, `/api/health`, `/api/profiles`. Renderer connects to service; shows "service ok" + profile list. Python supervisor (spawn / kill / restart-banner). `AgentSession` event-emission refactor (split TUI adapter from neutral emitter). | Integration is de-risked. Empty shell that proves the wire works. |
| **1 — Live command center (MVP)** | Full active-engagement flow end-to-end, one session at a time: new-engagement wizard → live chat → tool-call timeline → F-key skill picker → stop / done. KB browser (read-only). Findings list. Status bar with budget / turns. Sudo entry modal. Auth-gate confirmation modal. **Minimal resume affordance:** "resume my latest stopped session for this target" button on the new-engagement page. | **v1 ship target.** Replaces the TUI for live engagements that don't need the things Phase 3 adds. |
| **2 — Sessions & multi-engagement UX** | Full sessions sidebar (active / stopped / completed / abandoned filters across all targets). Browse and switch between *any* session (snapshot-and-resume — only one active at a time). Per-target dashboard summarizing hypotheses, findings, recent activity. Session detail page for completed engagements (read-only command-center view). | Delivers "switch engagements" and "review previous engagements". Phase 1 ships with only a same-target resume button; Phase 2 is the full cross-target browser. |
| **3 — Visual analytics** | Hypothesis tree as a real tree (react-arborist), status colors, live updates from WS. Screenshot evidence gallery. BloodHound graph view (Cytoscape, lazy-loaded — only starts Neo4j when opened). Scope.toml editor (form UI). Report preview (rendered Markdown from `kb_export_report`). | The TUI-impossible set. |
| **4 — Config, admin, polish** | Backend & API-key settings page (keychain integration). Health dashboard (MSF rpcd, Neo4j, Playwright Chromium, devenv shell sanity). Target hygiene UI (`--check-targets` equivalent). Report builder (edit notes, mark findings included, export PDF/Markdown). Packaging (macOS .app, Linux AppImage). Auto-update remains off by default. | "Complete" finishing pieces. |

**v1 = Phase 0 + Phase 1.** Phases 2–4 each get their own spec when their turn comes.

## 7. Project layout

New directories (no existing code is moved):

```
src/reverser/gui_service/
  __init__.py
  __main__.py             # entrypoint: parse args, find free port, mint token,
                          # write handshake line, run uvicorn
  app.py                  # FastAPI app factory
  auth.py                 # token middleware (REST + WS)
  config.py               # service config (host, port, project_root)
  session_adapter.py      # GUISession — wraps AgentSession, fans events to WS subscribers
  session_manager.py      # tracks live GUISessions, snapshot-and-resume swap
  routes/
    profiles.py
    backends.py
    health.py
    targets.py
    sessions.py
    settings.py
  ws/
    sessions.py           # WS endpoint per session

desktop/                  # Electron app
  package.json
  electron/
    main.ts               # window + Python supervisor + IPC handlers
    preload.ts            # contextBridge
    python.ts             # spawn / handshake / lifecycle
    keychain.ts           # safeStorage wrapper
  renderer/
    index.html
    src/
      App.tsx             # router
      api/
        client.ts         # fetch wrapper with bearer token
        queries.ts        # TanStack Query hooks
      state/
        stream.ts         # useSessionStream hook
        store.ts          # Zustand per-session store
      pages/
        Dashboard.tsx
        NewEngagement.tsx
        Session.tsx
        Settings.tsx
      panes/
        ChatPane.tsx
        ToolTimeline.tsx
        HypothesisPane.tsx
        FindingsPane.tsx
        KBPane.tsx
        EvidencePane.tsx
        ScopePane.tsx
        HealthPane.tsx
      components/         # shared shadcn-based pieces
      hooks/
      lib/
  vite.config.ts
  tsconfig.json
  tailwind.config.ts

docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md   # this file
docs/superpowers/plans/2026-05-13-plan-1-gui-service-foundation.md # forthcoming
docs/superpowers/plans/2026-05-13-plan-2-electron-shell.md          # forthcoming
docs/superpowers/plans/2026-05-13-plan-3-live-command-center.md     # forthcoming
```

`pyproject.toml` adds `fastapi`, `uvicorn[standard]` to dependencies and registers the `reverser.gui_service` module.

## 8. Testing strategy

### Python service

- **Unit:** standard pytest for handlers, with `AgentSession` mocked.
- **Integration:** `pytest-asyncio` + `httpx.AsyncClient` against the FastAPI `app` instance. Real `AgentSession`s with a stub backend that emits scripted `AgentEvent`s.
- **WS contract:** `websockets` client connects to the test app, asserts the event taxonomy stays stable. Snapshot tests on frame schemas.
- **Auth:** explicit tests that REST without bearer and WS without `?token` return 401.

### Renderer

- **Unit:** Vitest for hooks (`useSessionStream`, store reducers) and presentational components.
- **E2E:** Playwright (already in the project for webpentest) running against a dev Electron build with a mock service that scripts `AgentEvent` streams. Covers the new-engagement → chat → tool-timeline → stop happy path.

### Cross-stack smoke

- A `tests/smoke/gui_e2e.py` script that launches the real service + real Electron + real `AgentSession` with the `general` profile and a sample binary, asserts the chat pane receives at least one assistant message and one tool-call event within 30 seconds.

## 9. Packaging

- **In-development run:** `npm run dev` (Vite + Electron with hot-reload) + `python -m reverser.gui_service` started by the Electron main process. Requires the user to be in `devenv shell`.
- **Phase 4 packaging:** `electron-builder` for macOS `.app` and Linux AppImage. The packaged Electron app still requires the user to launch it from a `devenv shell` (so all 60+ native tools are on PATH). **The Python interpreter and native deps are not bundled** — `devenv shell` remains the supported environment.
- **Auto-update:** off by default. If revisited, signed-release infra is a separate spec.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| AgentSession event refactor breaks the TUI. | Refactor first, behind tests; the TUI adapter is a thin layer with no behavior change. Verify with the existing TUI happy path before any GUI code lands. |
| Local-attacker MITM on the loopback service. | Per-launch token; 127.0.0.1 binding; renderer sandbox; no token in renderer logs or devtools. |
| Token leaks via stdout to the wrong consumer. | Stdout handshake is *one* line; subsequent stdout is non-sensitive log lines. The token is also passed through Electron's IPC to the renderer, never written to a file. |
| Renderer falls behind on a fast event stream and OOMs. | Bounded ring-buffer in the Zustand store (e.g. 10k events per session); old events spilled to a paginated history endpoint. Tool-result `preview` is truncated server-side; full result fetched on demand. |
| Engagement state divergence between TUI and GUI. | Both go through the same `AgentSession` and `sessions.py` snapshot code. Snapshot is the contract. No GUI-only state lives outside the existing `targets/<target>/` tree. |
| Packaging the native Python stack into a single installer is too costly. | Out of scope. `devenv shell` is the documented prerequisite; packaging targets only the Electron UI. |

## 11. Out of scope (for future specs)

- Remote / multi-user access (would need real auth, TLS, role model).
- True parallel multi-session execution (one agent loop at a time in v1; UI is ready for it).
- Plugin / extension system for third-party panes.
- Mobile / tablet UI.
- Auto-update infrastructure.

## 12. Open questions

None blocking. Visual theme polish, exact shadcn component choices, and packaging signing strategy are all implementation-time decisions within their respective phases.
