# Phase 3a — Live Session Polish — Design Spec

**Date:** 2026-05-13
**Status:** Approved for planning
**Scope:** Hypothesis tree (live, view-only) and tool-call timeline replay (for completed sessions). First slice of Phase 3 (Visual analytics) per the original [desktop UI design](2026-05-13-electron-desktop-ui-design.md) and the Phase 2 design's "out of scope" list.

## 1. Goals & non-goals

### Goals

- Replace the flat hypothesis list with a real tree (parent/child) that respects the existing `HypothesisFact.parent_id` schema.
- Live-update the tree from the WebSocket `hypothesis` stream.
- For stopped / completed sessions, replay the session JSONL log into the store so the **tool timeline pane** and the **chat pane** populate with `tool_call`, `tool_result`, `thinking`, and `dispatch` events. The chat pane today only renders user/assistant text; this adds thinking + dispatch rendering for both the replay path and the live path (the live path inherits the same renderer for free).

### Non-goals (this phase)

- Hypothesis editing from the UI (view-only locked).
- Reordering / drag / right-click menus on the tree.
- Clickable links from `evidence_refs` to findings or screenshots (Phase 3b / 3c).
- Cross-session search across log events.
- BloodHound graph view (Phase 3c).
- Scope.toml editor, screenshot gallery, report preview (Phase 3b).
- Event-cap UI ("show more" beyond 5000) — Phase 4 if it ever matters.

## 2. Architecture

Phase 3a is frontend-heavy with one new backend endpoint.

### Backend (one new endpoint)

`GET /api/sessions/log/{id}?target={t}` reads the session's JSONL log via the existing `reverser.session_log.load_session_log(path)` helper. Filters to `{thinking, tool_call, tool_result, dispatch}` event kinds (drops `text` because the snapshot conversation already covers it, drops `result` because it's bookkeeping). Caps at 5000 events (oldest dropped). Returns 404 if the snapshot doesn't exist or the log file is gone.

### Frontend

Three components change, two new pieces appear:

- **`HypothesesPane`** (replace): currently a flat append list. New version uses `react-arborist` and builds the tree client-side from `HypothesisFact.parent_id`. Lives in the existing right-rail tab.
- **`ChatPane`** (modify): renders three streams (`messages`, `thinkingEntries`, `dispatchEntries`) interleaved by `turn`, then by timestamp within a turn. Each entry kind gets its own renderer.
- **`ToolTimelinePane`** (small modify): the empty-state copy switches based on a `replayed` flag in the store. The tool-call rendering itself is unchanged.
- **`useSessionLogReplay(sessionId, target)`** (new hook): mirrors the Phase 2 `useConversation` pattern. Only enabled when the session is not active; on data arrival, an effect in `SessionLayout` calls the store's `seedFromSessionLog` action.
- **`session-store`** (modify): new arrays `thinkingEntries`, `dispatchEntries`; a `replayed: boolean` flag; a new `seedFromSessionLog(events)` action; `ingest()` for `thinking` and `dispatch` frames is fleshed out (currently no-op); hypotheses move from an append-list to a `Map<id, HypothesisFact>` so live updates overwrite-by-id rather than duplicate.

## 3. Hypothesis tree

### Data flow

WS frames already arrive as `{type: "hypothesis", action: "add"|"update"|"refute"|"confirm", row: {…}}`. The store's reducer currently appends `row` to an array. Phase 3a changes the store to keep `Map<id, HypothesisFact>` keyed by `row.id` so subsequent updates overwrite. The pane builds a tree from `parent_id` on render.

A `useTargetKB(target)` query also returns hypotheses (refreshed every 8 s). For sessions that haven't streamed yet (e.g., resumed after a stop), the pane seeds from KB on mount and switches to the WS-driven Map once frames arrive.

### Visual conventions

- **Status color**: green = confirmed, amber = testing, red = refuted, gray = proposed/abandoned.
- **Strikethrough**: applied to refuted hypotheses (still legible; clearly failed).
- **Right-aligned hint**: dispatch count, child count, or status word (`testing · 1 child`).
- **Tree icons**: `▼` expanded, `▶` collapsed, blank for leaves.
- **Indent**: 16 px per level (react-arborist default).

### Live update behavior

- New `testing` / `confirmed` hypothesis: auto-expand its ancestor chain so it's visible.
- New `refuted` / `abandoned` hypothesis: don't change expansion state (avoid surprising the user).
- **No auto-scroll** — the user owns the scroll position.

### Interaction

- Single-click row: expand/collapse.
- Double-click statement: open a popover with `rationale`, `dispatched_to`, `dispatch_count`, `evidence_refs` (raw JSON for Phase 3a; clickable in Phase 3b+), `created_at`, `updated_at`.

## 4. Session-log replay

### Endpoint: `GET /api/sessions/log/{id}`

Required query param: `target=<name>`. The handler:

1. `sessions.load(target, session_id)` — same pattern as `/api/sessions/conversation/{id}`. Raises `SessionNotFoundError` → 404.
2. Reads `snap.log_path` from the snapshot.
3. If the file doesn't exist, returns 404 with `"log file not found: <path>"`.
4. `load_session_log(snap.log_path)` parses the JSONL into a list of dicts.
5. Filters to `{thinking, tool_call, tool_result, dispatch}` event kinds.
6. Caps to the **last 5000** events. If `len(filtered) > 5000`, sets `truncated: true` and drops the head (oldest events).
7. Returns:

```jsonc
{
  "id": "2026-05-12T22-54-46",
  "events": [
    { "kind": "thinking",    "content": "…", "turn": 1, "ts": "…" },
    { "kind": "tool_call",   "name": "nmap_scan", "input": "{…}", "turn": 1, "ts": "…" },
    { "kind": "tool_result", "preview": "…", "ok": true, "turn": 1, "ts": "…" },
    { "kind": "dispatch",    "specialty": "ad", "phase": "tool_call",
      "content": "ldap_search …", "turn": 2, "ts": "…" }
  ],
  "truncated": false
}
```

### Frontend hook

```ts
useSessionLogReplay(sessionId: string | null, target: string | null)
```

Mirrors `useConversation`:

- `enabled: ready && !!sessionId && !!target` AND only mounted by `SessionLayout` when `!isActive`.
- `staleTime: 5 * 60_000` (snapshot logs don't change).

On data arrival, `SessionLayout` calls `getSessionStore(id).getState().seedFromSessionLog(data.events)`.

### Store action: `seedFromSessionLog(events)`

```ts
seedFromSessionLog: (events) => set(() => {
  // 1. Clear existing replay-affected slots (idempotent re-mount safety)
  const toolCalls: ToolCall[] = [];
  const thinkingEntries: ThinkingEntry[] = [];
  const dispatchEntries: DispatchEntry[] = [];

  // 2. Re-apply each event into the right slot
  for (const e of events) {
    if (e.kind === "tool_call") {
      toolCalls.push({ /* synthesize id */ });
    } else if (e.kind === "tool_result") {
      // attach to the last open tool_call
    } else if (e.kind === "thinking") {
      thinkingEntries.push({ text: e.content, turn: e.turn, ts: e.ts, redacted: false });
    } else if (e.kind === "dispatch") {
      dispatchEntries.push({ /* … */ });
    }
  }

  return { toolCalls, thinkingEntries, dispatchEntries, replayed: true };
}),
```

Idempotent: calling `seedFromSessionLog` twice produces the same final state.

## 5. ChatPane render

The pane derives an `entries` array by merging three sources, sorted by `(turn, ts, insertion-order)`:

```ts
type ChatEntry =
  | { kind: "user";       text: string; turn?: number }
  | { kind: "assistant";  text: string; turn?: number }
  | { kind: "thinking";   text: string; turn: number;
                          ts: string; redacted: boolean }
  | { kind: "dispatch";   specialty: string; phase: string;
                          content: string; turn: number; ts: string };
```

### Per-kind rendering

- **user / assistant**: unchanged from today.
- **thinking**: collapsed row by default. One row per *turn-that-has-thinking-events*, showing `▸ thinking · turn N` with a `[show]` button. Click → expand to reveal all thinking events for that turn as italic dim text. (Grouping by turn keeps the chat readable; an engagement turn may emit many thinking events.)
- **dispatch**: `[specialty]` magenta-400 prefix matching the TUI convention. Body text colored by `phase`:
  - `text` → neutral
  - `tool_call` → cyan
  - `tool_result` → dim green
  - `tool_error` / `error` → dim red
  - `thinking` → dim italic

### Live path behavior

The same `ingest()` reducer handles WS thinking and dispatch frames. So this is not a replay-only feature — when a live session emits `thinking` or `dispatch` frames, the chat pane renders them the same way.

### Performance

Merge is O(N log N) once per render; React's memoization keeps it cheap as long as the three source arrays haven't changed. The store keeps them separate so individual updates from the live stream don't force a re-sort across all three.

## 6. File layout

### Backend

```
src/reverser/gui_service/routes/sessions.py        modify  (add /api/sessions/log/{id})
tests/gui_service/test_session_log_replay.py       create  (~4 tests)
```

### Frontend

```
desktop/package.json                            modify  (add react-arborist@^3)
desktop/renderer/src/
  api/client.ts                                 modify  (+ SessionLogResponse, LogEvent types)
  api/queries.ts                                modify  (+ useSessionLogReplay)
  state/session-store.ts                        modify  (+ thinkingEntries, dispatchEntries,
                                                          replayed flag, seedFromSessionLog,
                                                          flesh out ingest for thinking + dispatch,
                                                          Map<id, HypothesisFact> for hypotheses)
  panes/ChatPane.tsx                            modify  (merge three streams, render
                                                          thinking + dispatch)
  panes/HypothesesPane.tsx                      replace (react-arborist tree)
  panes/ToolTimelinePane.tsx                    modify  (empty-state copy switches on
                                                          replayed flag)
  layout/SessionLayout.tsx                      modify  (call useSessionLogReplay on
                                                          read-only sessions; seed on data)
  tests/e2e/phase3a.spec.ts                     create  (~4 Playwright tests)
```

`react-arborist@^3` is the only new npm dep. It was named in the Phase 0 file map but never installed.

## 7. Testing

### Backend (pytest)

- `test_log_empty_returns_no_events` — empty JSONL file → `{events: [], truncated: false}`.
- `test_log_filters_to_allowed_kinds` — log with mixed kinds → only `{thinking, tool_call, tool_result, dispatch}` returned, preserved in order.
- `test_log_truncates_above_cap` — log with 6000 events → 5000 returned, `truncated: true`, oldest 1000 dropped.
- `test_log_404_missing_snapshot` — unknown session_id → 404.
- `test_log_404_missing_log_file` — snapshot exists but `log_path` file is gone → 404 with descriptive detail.
- `test_log_422_missing_target_query` — required query param missing → 422 (FastAPI validation).

### Frontend (Playwright e2e)

Building on the Phase 2 `phase2.spec.ts` pattern (real Electron + spawned Python):

- `test_hypothesis_tree_renders_for_target` — pre-seed a target KB with parent/child hypotheses, open a session, click Hypotheses tab → tree visible with at least one nested child.
- `test_tool_timeline_seeded_for_stopped_session` — open a stopped session that has a tool-call log → timeline pane shows the tool calls.
- `test_chat_renders_dispatch_with_prefix` — same stopped session that logged dispatch events → chat shows `[specialty]` prefix in a magenta color.
- `test_thinking_row_collapsed_by_default` — same session that logged thinking events → row visible, content hidden until `[show]` is clicked.

The new e2e spec follows the Phase 2 PATH-injection pattern (the bin/python shim).

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `react-arborist` is a meaningful new dep; we haven't proven the rendering at scale. | 100-node typical case is well within library limits. If a target accumulates 10k hypotheses (unlikely), Phase 4 polish can virtualize or paginate. |
| Session log files can be large (>100 MB for a multi-day engagement). Reading the whole file just to filter four event kinds is wasteful. | Acceptable for Phase 3a — read is one-shot at mount and bounded by the 5000-event cap. If profiling shows a problem, add a streaming-tail variant in Phase 4. |
| `seedFromSessionLog` clears `toolCalls` etc. before re-applying. If the route re-mounts while a WS is also streaming (unlikely with our gating, but possible), live events between mounts could be dropped. | The hook is only mounted for non-active sessions; the WS is closed when `!isActive` so there are no live events to lose. Documented; defer to Phase 4. |
| Chat pane's three-stream merge re-sorts on every store change. | The merge runs in a memoized selector; React's `useMemo` over stable input arrays makes this cheap. Worst case is a single replay-then-stable interaction. |
| Hypothesis tree's auto-expand-ancestors behavior could surprise users in pathological cases (e.g., 100 testing hypotheses all expanding at once). | Auto-expand only fires for the *changed* node's ancestors, not globally. Bounded chain length. |

## 9. Out of scope (for future phases)

- Hypothesis editing from the UI — view-only locked.
- Linking `evidence_refs` to findings / screenshots — Phase 3b or 3c.
- BloodHound graph view — Phase 3c.
- Screenshot evidence gallery, scope.toml editor, report preview — Phase 3b.
- Event-cap UI ("show more" pagination beyond 5000) — Phase 4 if needed.
- Cross-session search across log events — Phase 4.
- Streaming-tail variant of the log endpoint — Phase 4 if profiling indicates a need.

## 10. Open questions

None blocking.
