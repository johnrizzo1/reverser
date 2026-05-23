# Chat Window Redesign + KB Reactivity + F-Key Wiring — Design Spec

**Date:** 2026-05-23
**Status:** Approved for planning
**Scope:** Rework the live session main pane from a single accumulating wall of text into a per-turn chat-window layout with nested dispatch sub-conversations; make hypothesis and finding writes reflect in the UI without a page refresh; wire the F2 keybinding that the footer already advertises.

## 1. Goals & non-goals

### Goals

- Each agent turn renders as its own bubble in the chat. Thinking is visually distinct from speech and collapsed by default. Tool calls render inline inside the turn bubble, replacing the separate TOOL TIMELINE pane.
- `dispatch_specialist` calls render as a nested mini-chat inside the parent turn bubble, with the sub-agent's own turn bubbles (thinking, speech, tool calls).
- The Hypotheses and Findings panes update live as the agent writes to the KB during a running engagement — no manual refresh, no tab-switch workaround. Stale read-only sessions still get a correct snapshot on mount.
- The F-keys advertised in the footer (`F1 skills | F2 profile | F4 sudo | F6 stop`) all respond. Unused F-key labels are removed from any surface that shows them. Exactly one footer renders the keymap.

### Non-goals (this phase)

- Live updates for other KB sections (hosts, services, credentials, artifacts, notes). Those remain on the existing snapshot-on-mount + manual-tab-switch refetch model.
- Markdown rendering inside thinking blocks. Thinking renders as preserve-whitespace plain text.
- Wiring F3 or F5. They are not advertised and not added.
- Refactoring the existing session-log replay format. We synthesize ids and turn boundaries during replay of pre-existing logs.
- A reusable cross-pane bubble component library — the `<TurnBubble>` recursion is internal to the ChatPane subtree.
- Generic `kb_update` frame plumbing. We emit specific `hypothesis` / `finding` frames; the renderer's existing dead `kb_update` reducer case is removed rather than fleshed out.

## 2. Architecture

The change has four moving parts: the WS frame protocol, the renderer session store, the ChatPane component tree, and the KB tool emit path.

### 2.1 WS frame protocol changes

The protocol stays JSON-over-WS, additive. Existing frame types keep their shape; we add fields and one new lifecycle. Reference points are `desktop/renderer/src/state/session-store.ts` (`WSFrame` union) and `src/reverser/gui_service/session_adapter.py` (`_event_to_frame`).

**Frames that gain a `turn` field:**

```
{ type: "text",        role: "assistant", delta: string,                      turn: number }
{ type: "thinking",    delta: string, redacted: boolean,                       turn: number }
{ type: "tool_call",   name: string, args: string, tool_use_id: string,        turn: number }
{ type: "tool_result", ok: boolean, preview: string, tool_use_id: string,      turn: number }
```

`tool_use_id` is the Claude Agent SDK's `ToolUseBlock.id` — the renderer uses it to pair calls with results explicitly instead of the current last-unmatched-by-position heuristic at `session-store.ts` line ~194.

**Dispatch frames gain `dispatch_id`, `sub_turn`, and explicit lifecycle phases:**

```
{ type: "dispatch", dispatch_id: string, turn: number,
  phase: "start", specialty: string, hypothesis_id?: number, sub_goal: string }

{ type: "dispatch", dispatch_id: string, turn: number, sub_turn: number,
  phase: "text" | "thinking" | "tool_call" | "tool_result" | "tool_error",
  content: string }

{ type: "dispatch", dispatch_id: string, turn: number,
  phase: "end", status: string, cost: number, turns: number }
```

`dispatch_id` is a UUID minted by the dispatch tool at entry. `sub_turn` is incremented on every `AssistantMessage` boundary inside the sub-agent's `async for message in query(...)` loop (`src/reverser/tools/dispatch.py` line ~359).

**New KB frames:**

```
{ type: "hypothesis", action: "create" | "update", row: HypothesisRow }
{ type: "finding",    action: "create" | "update", row: FindingRow }
```

The renderer reducer overwrites by `row.id`. The `hypothesis` frame shape already exists in the current `WSFrame` union and is partially handled at `session-store.ts:224`; we make it correct end-to-end. The `finding` frame is new on both sides.

**Removed:** the `kb_update` case in the renderer reducer (currently a no-op at `session-store.ts:247`).

**Backwards compatibility:** none required. WS frames are transient; renderer and backend update in lockstep. The renderer silently drops unknown frame types today, so a transient mismatch during deploy is harmless.

### 2.2 Backend wiring

Three small backend touches:

1. **`src/reverser/agent_session.py`** — the `_on_dispatch_event` callback signature expands from `(specialty, kind, content)` to `(specialty, dispatch_id, sub_turn, kind, content)`. A new sibling callback slot `on_kb_event: Callable[[str, dict], None] | None` is added, mirroring `on_dispatch_event`. The AgentSession also threads the current turn counter into `text`/`thinking`/`tool_call`/`tool_result` AgentEvents so the adapter can stamp them onto the frame.

2. **`src/reverser/gui_service/session_adapter.py`** — `_event_to_frame` reads the turn from the AgentEvent and includes it on the frame. `_on_dispatch_event` is updated to the new signature; `_on_kb_event` is added and publishes `hypothesis` / `finding` frames. `GUISession.__init__` attaches both callbacks.

3. **`src/reverser/tools/dispatch.py`** — `_emit` mints a `dispatch_id` once at entry, tracks `sub_turn` as it iterates messages, and emits `start` / `end` frames bracketing the body. The existing `_slog.log_dispatch_event(...)` call still records the kind+content for replay; the new ids are logged alongside so replay can reconstruct dispatch grouping.

4. **`src/reverser/tools/kb.py`** — every hypothesis/finding mutator (`kb_propose_hypothesis`, `kb_update_hypothesis`, `kb_record_finding`, and any other mutator surfaced by grep in implementation) calls a new helper `emit_hypothesis(action, row)` or `emit_finding(action, row)` after the SQLite write. The helper lives in a new module `src/reverser/gui_service/kb_emitter.py` and is a no-op when `current_session.get()` returns None (pure-CLI / headless paths unaffected).

The KB emit helper is intentionally simple — it looks up the session via `current_session` (the same context var dispatch already uses) and calls `sess.on_kb_event(kind, payload)` if attached. Fire-and-forget: a publish failure never breaks the tool path.

### 2.3 Renderer session store

The store at `desktop/renderer/src/state/session-store.ts` is the largest single-file change. The flat lists are replaced with a turn-indexed map.

```ts
type ToolCall = {
  id: string;                  // tool_use_id from backend
  name: string;
  args: string;
  result?: { ok: boolean; preview: string };
};

type SubTurn = {
  thinkingDeltas: string[];
  speechDeltas: string[];
  toolCalls: { name: string; content: string }[];  // sub-agent tools display-only
  toolResults: { ok: boolean; content: string }[];
};

type Dispatch = {
  id: string;                  // dispatch_id from backend
  specialty: string;
  hypothesisId?: number;
  subGoal: string;
  status: "running" | "completed" | "error";
  cost?: number;
  turnsConsumed?: number;
  subTurns: Map<number, SubTurn>;
};

type Turn = {
  turn: number;
  userMessage?: string;        // user prompt that opened this turn, if any
  thinkingDeltas: string[];
  speechDeltas: string[];
  toolCalls: Map<string, ToolCall>;       // keyed by tool_use_id
  dispatches: Map<string, Dispatch>;       // keyed by dispatch_id
  status: "streaming" | "done";
  ordering: Array<                         // append-only render order
    | { kind: "thinking"; index: number }
    | { kind: "speech";   index: number }
    | { kind: "tool";     id: string }
    | { kind: "dispatch"; id: string }>;
};

type SessionState = {
  status: "idle" | "running" | "awaiting_input" | "stopped" | "completed" | "error";
  turns: Map<number, Turn>;
  currentTurn: number;
  hypotheses: Map<number, HypothesisRow>;
  findings: Map<number, FindingRow>;       // changed from `unknown[]`
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  log: { level: string; msg: string; ts: number }[];
  replayed: boolean;
};
```

**Removed fields:** `messages`, `pendingAssistantText`, `thinkingEntries`, `dispatchEntries`. Any consumer of those is migrated to read from `turns`.

**Reducer cases:**

- `text { delta, turn }` → push delta into `turns.get(turn).speechDeltas`. If `turns.get(turn).ordering.at(-1)` isn't a `speech` entry, append a new one.
- `thinking { delta, turn }` → analogous, with `thinkingDeltas`.
- `tool_call { name, args, tool_use_id, turn }` → `turns.get(turn).toolCalls.set(tool_use_id, {...})`; append `{kind: "tool", id: tool_use_id}` to ordering.
- `tool_result { ok, preview, tool_use_id, turn }` → look up the call and set `.result`. No ordering change. If the call isn't found, drop and console.warn.
- `dispatch { phase: "start", dispatch_id, turn, specialty, hypothesis_id, sub_goal }` → `turns.get(turn).dispatches.set(dispatch_id, {...status: "running"})`; append `{kind: "dispatch", id: dispatch_id}` to ordering.
- `dispatch { phase: "text"|"thinking"|"tool_call"|"tool_result"|"tool_error", dispatch_id, turn, sub_turn, content }` → drill into `turns.get(turn).dispatches.get(dispatch_id).subTurns.get(sub_turn)` (lazy-create) and append to the right list.
- `dispatch { phase: "end", dispatch_id, turn, status, cost, turns: turnsConsumed }` → set status/cost/turnsConsumed on the Dispatch.
- `hypothesis { action, row }` → `hypotheses.set(row.id, row)`.
- `finding { action, row }` → `findings.set(row.id, row)`.
- `status { phase: "running", turns: N }` → ensure `turns.get(N)` exists with `status: "streaming"`; mark the previous turn `done`; set `currentTurn = N`.
- `status { phase: "awaiting_input" | "stopped" | "completed" }` → mark `currentTurn` turn `done`; update top-level `status`.
- `budget`, `conn_breaker`, `log` → unchanged.
- `kb_update` → removed.

**User input.** `appendUserMessage(text)` attaches the text to `turns.get(currentTurn + 1)?.userMessage` (lazy-create the turn entry). The backend's next `status: running, turns: N` will land on the same N.

**Replay seed.** `seedFromSessionLog(events)` is updated to assemble the same `turns: Map`. If the log doesn't already carry turn / tool_use_id / dispatch_id, the replay synthesizes them by counting turn-boundary markers and using positional synthetic ids of the form `syn-{name}-{idx}`. New sessions write the real ids into the log so future replays don't need synthesis (a small additional change in the session-log writer).

`seedHypotheses(rows)` is unchanged. New `seedFindings(rows)` action mirrors it.

### 2.4 ChatPane component tree

The pane is rewritten around the new data model. New file layout under `desktop/renderer/src/panes/`:

- `ChatPane.tsx` — top-level container, scroll handling, composer.
- `TurnBubble.tsx` — one agent turn. Renders thinking chip, speech block, tool chips, nested dispatch panels. Recursively used inside dispatch panels.
- `DispatchPanel.tsx` — header + body for a nested specialist sub-conversation. Body renders `<SubTurnBubble>` entries.
- `SubTurnBubble.tsx` — internal grammar matches `TurnBubble` but reads from `SubTurn` (no nested dispatches inside dispatches — the tool path forbids it).
- `UserBubble.tsx` — right-aligned user message, extracted for clarity.

**Top-level rendering:**

```
<ChatPane>
  <ScrollArea>
    {Array.from(turns.values()).sort((a, b) => a.turn - b.turn).map(turn => (
      <Fragment key={turn.turn}>
        {turn.userMessage && <UserBubble text={turn.userMessage} />}
        <TurnBubble turn={turn} />
      </Fragment>
    ))}
  </ScrollArea>
  <Composer />
</ChatPane>
```

**TurnBubble internals.** For each entry in `turn.ordering`:

- `thinking` → on first occurrence in this turn, render one `<ThinkingChip count={thinkingDeltas.length}>`; subsequent `thinking` ordering entries are no-ops (the chip already shows them all when expanded). This intentionally consolidates rather than interleaves — thinking is rarely interleaved with speech in a way worth fragmenting the UI over.
- `speech` → render a `<SpeechBlock>` with markdown that concatenates consecutive speech deltas. A new speech ordering entry only appears after a tool/dispatch breaks the stream, so this typically yields one block per turn.
- `tool` → `<ToolCallChip>` — collapsed shows `✓ {name} {args.slice(0, 80)}…`; expanded shows full args (formatted JSON) and `result.preview` styled by ok/err.
- `dispatch` → `<DispatchPanel dispatch={d} />`. Always expanded by default — the nested mini-chat is the point of the choice.

**Visual treatment.**

- User bubble: right-aligned, neutral-800 background, max-width 75% — unchanged from current.
- Agent turn bubble: left-aligned, no background fill, vertical left border (1px neutral-700).
- Nested dispatch panel: deeper neutral-700 left border + 16px indent.
- Streaming turn: small `●` pulse next to the turn header.
- Empty turn (e.g., only a dispatch, no manager speech): still rendered.

**Markdown.** `react-markdown` for speech blocks. Thinking renders as `<pre>` preserve-whitespace plain text.

**Auto-scroll.** Scroll to bottom on new turn or new delta if the user is already within ~100px of the bottom; otherwise show a "↓ new messages" floating chip. Improvement over the current "always scroll" but in scope because the pane is being rewritten.

**Composer.** Existing Textarea + Send + ⌘/Ctrl+Enter shape stays.

### 2.5 Tool Timeline removal

`desktop/renderer/src/panes/ToolTimelinePane.tsx` is deleted. The slot for it in `desktop/renderer/src/layout/SessionLayout.tsx` is removed. The F1/F4/F6 button row stays in its current spot in `SessionLayout`.

If any tests reference the timeline pane (snapshot tests, e2e selectors), they're updated to point at the new chat-pane structures.

### 2.6 F-keys + footer

**SessionLayout keymap.** The `useEffect` keydown handler at `SessionLayout.tsx` line ~52 binds F1/F2/F4/F6. New:

```ts
if (e.key === "F2") { e.preventDefault(); setProfileOpen(true); }
```

The button row at line ~128 gains a `Profile (F2)` button mirroring the others.

**Profile picker modal.** New file `desktop/renderer/src/modals/ProfilePickerModal.tsx`, modeled on `SkillPickerModal.tsx`. It:

- Lists available profiles via a query (reuses existing if present in `api/queries.ts`; adds `useProfiles()` if not).
- Shows the current session's profile with a checkmark.
- Apply button calls `PATCH /api/sessions/{id}/config` with `{profile: newKey}`. The backend already validates profile/backend coupling per recent commit `4a3eb71`; we surface validation errors inline.
- Apply is disabled when `status === "running"` — the existing config-edit gate.

**Footer dedup.** `desktop/renderer/src/layout/Footer.tsx` becomes the single source of truth for the keymap and lists `F1 skills · F2 profile · F4 sudo · F6 stop`. Implementation audits `Shell.tsx` / `SessionLayout.tsx` for duplicate Footer mounts and removes any extras. F3 and F5 are not mentioned anywhere.

## 3. Error handling & edge cases

- **Tool result before its tool_call** (out-of-order frames): `tool_use_id` lookup fails → drop the result, `console.warn`. Today's positional heuristic silently mismatches in this case; explicit ids surface the bug if it ever happens.
- **Dispatch `end` without `start`**: drop and console.warn. No map entry to mutate.
- **Late frames after a turn is marked `done`**: still slot into the turn's data; the bubble re-renders. Status stays `done` (the streaming pulse doesn't reappear).
- **Agent crashes mid-turn**: backend eventually sends `status: stopped` or `error`. The open turn keeps its partial deltas, gets marked `done`, and a small `incomplete — agent stopped` footer appears in that bubble.
- **Pre-existing session log replay**: `seedFromSessionLog` synthesizes ids and turn boundaries by counting turn markers and using positional synthetic ids (`syn-{name}-{idx}`). New sessions write real ids into the log so the synthesis path stays cold for them.
- **KB emit while no session is current**: `current_session.get()` returns None → helper returns immediately. Headless CLI tool invocations are unaffected.
- **F2 pressed during a running turn**: profile picker opens, but the Apply button is disabled with the existing "pause first" UX.
- **Publish failure on the bus**: KB and dispatch emit paths catch and ignore. KB row will still appear via the next snapshot refetch (e.g., on tab switch). No retry.

## 4. Testing strategy

### Unit (Vitest, `desktop/renderer/src/...`)

- `state/session-store.test.ts` — feed a fixture sequence of frames and assert resulting `turns: Map` shape. One test per reducer case. Out-of-order frames and duplicate frames covered. `seedFromSessionLog` synthesis path covered.
- `panes/chat-pane.test.tsx` — snapshot a turn in each of the canonical combinations: speech only; thinking + speech; speech + tool; speech + dispatch with sub-turns. Assert collapsibles toggle on click. Assert markdown rendering inside speech blocks.
- `modals/profile-picker-modal.test.tsx` — render, select a profile, assert PATCH fires with the right body. Assert Apply disabled when status=running.

### Backend (pytest)

- `tests/gui_service/test_session_adapter.py` — feed `AgentEvent` fixtures (now carrying turn) through `_event_to_frame` and assert WS frame shape. Assert dispatch frames carry `dispatch_id`/`sub_turn`/lifecycle phases when synthesized through the dispatch callback path.
- `tests/tools/test_kb_emit.py` — invoke `kb_update_hypothesis` (and the other mutators) with a mocked `current_session` whose `on_kb_event` is a spy. Assert spy receives `hypothesis` / `finding` events with the full row.
- `tests/gui_service/test_session_log_replay.py` — feed a pre-existing-format session log into the replay path and assert no crash; assert synthetic ids appear.

### E2E (Playwright)

- F2 opens the profile picker modal; selecting a different profile fires the PATCH and the picker closes.
- A turn bubble renders a thinking chip; clicking it expands to show thinking text.
- A `dispatch_specialist` tool call renders as a nested sub-conversation rather than a chip.
- The Hypotheses pane row updates in place when a `kb_update_hypothesis` happens during an active session (without refresh).

## 5. Out of scope (reaffirmed)

- Live updates for hosts / services / credentials / artifacts / notes KB sections.
- Markdown in thinking blocks.
- F3 / F5 keybindings.
- Refactoring the snapshot/replay file format itself.
- Cross-pane reusable bubble component library.
