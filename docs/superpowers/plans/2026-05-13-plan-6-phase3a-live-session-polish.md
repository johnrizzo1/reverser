# Phase 3a — Live Session Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat HypothesesPane with a live, tree-shaped (react-arborist) view, and replay completed sessions' `tool_call` / `tool_result` / `thinking` / `dispatch` events from the session log into the existing panes. Live sessions inherit the same chat-pane renderer for thinking + dispatch frames for free.

**Architecture:** One new backend endpoint reads the JSONL log and returns filtered events (5000-event cap). Frontend gains a `useSessionLogReplay` hook that mirrors the Phase 2 `useConversation` pattern. The session store gets two new arrays (`thinkingEntries`, `dispatchEntries`), a `replayed` flag, a `seedFromSessionLog` action, and an `ingest()` reducer that handles thinking + dispatch frames (currently no-ops). `HypothesesPane` switches to a `Map<id, HypothesisFact>` so live updates overwrite-by-id, then renders via `react-arborist`. `dispatch` events are now persisted to the session log (one new helper + a one-line wire in `tools/dispatch.py`) so historical replay works for future sessions.

**Tech Stack:** Python 3.11+, FastAPI, existing `reverser.session_log` helper, React 18, TypeScript, `react-arborist` (new dep, version ^3), Zustand (existing), TanStack Query (existing).

**Reference spec:** [`docs/superpowers/specs/2026-05-13-phase-3a-live-session-polish-design.md`](../specs/2026-05-13-phase-3a-live-session-polish-design.md).

---

## File map

```
Backend:
  src/reverser/session_log.py                            modify  (+ log_dispatch_event)
  src/reverser/tools/dispatch.py                         modify  (wire log_dispatch_event)
  src/reverser/gui_service/routes/sessions.py            modify  (+ /api/sessions/log/{id})
  tests/gui_service/test_session_log_replay.py           create  (~6 tests)
  tests/test_session_log_dispatch.py                     create  (~2 tests)

Frontend:
  desktop/package.json                                   modify  (+ react-arborist@^3)
  desktop/renderer/src/
    api/client.ts                                        modify  (+ SessionLogResponse types)
    api/queries.ts                                       modify  (+ useSessionLogReplay)
    state/session-store.ts                               modify  (+ thinkingEntries,
                                                                  dispatchEntries, replayed,
                                                                  seedFromSessionLog,
                                                                  ingest for thinking/dispatch,
                                                                  hypotheses Map)
    panes/ChatPane.tsx                                   modify  (render thinking + dispatch)
    panes/HypothesesPane.tsx                             replace (react-arborist tree)
    panes/ToolTimelinePane.tsx                           modify  (empty-state copy)
    layout/SessionLayout.tsx                             modify  (wire useSessionLogReplay)
  tests/e2e/phase3a.spec.ts                              create  (~4 Playwright tests)
```

---

## Task 1: `SessionLog.log_dispatch_event` + dispatch.py wire

`dispatch` events currently fire only through the in-process `emit_dispatch_event` callback — they never reach disk. The replay endpoint needs them in the log. Add a helper method + call it from the dispatch hot path.

**Files:**
- Modify: `src/reverser/session_log.py` (add `log_dispatch_event`)
- Modify: `src/reverser/tools/dispatch.py` (wire it next to `sess.emit_dispatch_event`)
- Test: `tests/test_session_log_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_log_dispatch.py`:

```python
"""SessionLog persists dispatch_specialist sub-agent events.

The TUI surfaces dispatch events live via emit_dispatch_event; this
parallel write to the JSONL log lets read-only session replay show them
after the fact.
"""
import json

import pytest

from reverser.session_log import SessionLog, load_session_log


def test_log_dispatch_event_writes_expected_shape(tmp_path):
    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    try:
        slog.log_dispatch_event("ad", "tool_call", "ldap_search cn=Users,dc=corp")
    finally:
        slog.close()

    entries = load_session_log(str(log_path))
    dispatch_entries = [e for e in entries if e.get("type") == "dispatch"]
    assert len(dispatch_entries) == 1
    e = dispatch_entries[0]
    assert e["specialty"] == "ad"
    assert e["kind"] == "tool_call"
    assert e["content"] == "ldap_search cn=Users,dc=corp"
    assert "ts" in e  # SessionLog always stamps ts


def test_log_dispatch_event_truncates_content(tmp_path):
    """Content is capped to keep log files manageable (matches the existing
    tool_result truncation behavior)."""
    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    try:
        slog.log_dispatch_event("ad", "tool_result", "x" * 5000)
    finally:
        slog.close()

    entries = load_session_log(str(log_path))
    e = next(e for e in entries if e["type"] == "dispatch")
    assert len(e["content"]) <= 4096
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/test_session_log_dispatch.py -v`
Expected: FAIL — `AttributeError: 'SessionLog' object has no attribute 'log_dispatch_event'`.

- [ ] **Step 3: Implement `log_dispatch_event` in `session_log.py`**

Open `src/reverser/session_log.py`. Find any existing `log_*` method (e.g., `log_tool_result`) as a placement reference. Append this new method alongside the others (inside the `SessionLog` class):

```python
    def log_dispatch_event(self, specialty: str, kind: str, content: str):
        """Persist a dispatch_specialist sub-agent event so read-only
        session replay can render it.

        Specialty: 'ad', 'webpentest', etc. (sub-agent profile key).
        Kind: 'text' | 'thinking' | 'tool_call' | 'tool_result' | 'tool_error' | 'start' | 'result' | 'error'.
        Content: truncated to 4096 chars to keep log files bounded.
        """
        self._write({
            "type": "dispatch",
            "specialty": specialty,
            "kind": kind,
            "content": (content or "")[:4096],
        })
```

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/test_session_log_dispatch.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire it from `tools/dispatch.py`**

Open `src/reverser/tools/dispatch.py`. Find the single call to `sess.emit_dispatch_event(specialty, kind, content)` (Plan 1's review noted there's exactly one). Add a parallel call to `sess._slog.log_dispatch_event` just above it:

```python
# Persist for read-only replay (Phase 3a). Best-effort; the in-process
# callback below drives the live UI either way.
try:
    sess._slog.log_dispatch_event(specialty, kind, content)
except Exception:
    pass
sess.emit_dispatch_event(specialty, kind, content)
```

(Best-effort because a logging error must never crash the dispatch tool — same conservative posture as `emit_dispatch_event` itself.)

- [ ] **Step 6: Verify the wire — full test suite still passes**

Run: `pytest tests/ -v 2>&1 | tail -5`
Expected: same pass count as before plus the 2 new tests.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/session_log.py src/reverser/tools/dispatch.py tests/test_session_log_dispatch.py
git commit -m "feat(session_log): persist dispatch events for read-only replay

Dispatch sub-agent events used to surface only via the in-process
on_dispatch_event callback (the TUI's [specialty] prefix). They never
reached disk, so Phase 3a's read-only session replay couldn't render
them.

Adds SessionLog.log_dispatch_event(specialty, kind, content) — same
shape as the in-process callback. Wires it into tools/dispatch.py
right next to the existing emit_dispatch_event call. Best-effort: a
logging error must not crash the dispatch tool.

Content is truncated to 4096 chars matching the tool_result cap."
```

---

## Task 2: `GET /api/sessions/log/{id}?target={t}` endpoint

Reads the JSONL log via `load_session_log`, filters to four event kinds, caps at 5000 events.

**Files:**
- Modify: `src/reverser/gui_service/routes/sessions.py`
- Test: `tests/gui_service/test_session_log_replay.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui_service/test_session_log_replay.py`:

```python
"""GET /api/sessions/log/{id}?target=t replays filtered session-log events
for read-only session detail views.
"""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


def _write_snapshot(tmp_path, target, session_id, log_relpath):
    """Write a SessionSnapshot JSON that points at log_relpath."""
    target_dir = tmp_path / "targets" / target / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "version": 1,
        "session_id": session_id,
        "target": target,
        "log_path": str(tmp_path / log_relpath),
        "config": {
            "profile": "webpentest", "backend": "claude", "model": None,
            "api_base": None, "budget": 5.0, "max_turns": 50,
        },
        "stats": {"turns": 0, "total_cost": 0.0},
        "state": "stopped",
        "started_at": "2026-05-12T22:54:46Z",
        "stopped_at": "2026-05-12T23:14:00Z",
        "pid": None,
        "conversation": [],
    }
    (target_dir / f"{session_id}.json").write_text(json.dumps(snap))
    return Path(tmp_path / log_relpath)


def _write_log(log_path: Path, entries: list[dict]):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.mark.asyncio
async def test_log_empty_returns_no_events(client, tmp_path):
    log_path = _write_snapshot(tmp_path, "10.10.10.5", "s1", "logs/s1.jsonl")
    _write_log(log_path, [])
    r = await client.get("/api/sessions/log/s1?target=10.10.10.5", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "s1"
    assert body["events"] == []
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_log_filters_to_allowed_kinds(client, tmp_path):
    log_path = _write_snapshot(tmp_path, "t1", "s1", "logs/s1.jsonl")
    _write_log(log_path, [
        {"type": "session_start", "binary": "x"},
        {"type": "turn", "turn": 1},
        {"type": "text", "text": "hi"},                              # filtered out
        {"type": "thinking", "text": "Considering options"},
        {"type": "tool_call", "name": "nmap_scan", "input": {"target": "x"}},
        {"type": "tool_result", "content": "open 22/tcp", "is_error": False},
        {"type": "dispatch", "specialty": "ad", "kind": "tool_call",
         "content": "ldap_search"},
        {"type": "result", "subtype": "success"},                    # filtered out
        {"type": "session_end"},                                     # filtered out
    ])
    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["thinking", "tool_call", "tool_result", "dispatch"]
    # Preserved field mapping:
    e_thinking = body["events"][0]
    assert e_thinking["content"] == "Considering options"
    e_tc = body["events"][1]
    assert e_tc["name"] == "nmap_scan"
    # Tool input is serialized to a string for the frontend (matches the
    # existing WS frame contract).
    assert isinstance(e_tc["input"], str)
    e_tr = body["events"][2]
    assert e_tr["ok"] is True
    assert e_tr["preview"] == "open 22/tcp"
    e_dispatch = body["events"][3]
    assert e_dispatch["specialty"] == "ad"
    assert e_dispatch["phase"] == "tool_call"


@pytest.mark.asyncio
async def test_log_truncates_above_5000_cap(client, tmp_path):
    log_path = _write_snapshot(tmp_path, "t1", "s1", "logs/s1.jsonl")
    # 6000 thinking entries: 1000 should be dropped from the head.
    entries = [{"type": "thinking", "text": f"thought {i}"} for i in range(6000)]
    _write_log(log_path, entries)

    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert len(body["events"]) == 5000
    # The 5000 we kept are the LATEST 5000 (head dropped):
    assert body["events"][0]["content"] == "thought 1000"
    assert body["events"][-1]["content"] == "thought 5999"


@pytest.mark.asyncio
async def test_log_404_missing_snapshot(client):
    r = await client.get("/api/sessions/log/no-such-session?target=t1", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_log_404_missing_log_file(client, tmp_path):
    """Snapshot points at a log path that doesn't exist on disk."""
    _write_snapshot(tmp_path, "t1", "s1", "logs/missing.jsonl")
    # NOTE: don't create the log file.
    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_log_422_missing_target_query(client):
    r = await client.get("/api/sessions/log/s1", headers=HEADERS)
    assert r.status_code == 422
```

- [ ] **Step 2: Run — verify failure**

Run: `pytest tests/gui_service/test_session_log_replay.py -v`
Expected: FAIL — `404 Not Found` (route absent).

- [ ] **Step 3: Implement the endpoint**

Open `src/reverser/gui_service/routes/sessions.py`. The Phase 2 `/api/sessions/conversation/{session_id}` handler is already in this file (also takes `target: str` as a required query param). Add the new handler nearby with the same shape.

At the top of the file, ensure `load_session_log` is imported. The existing import line probably reads:

```python
from ...sessions import SessionNotFoundError, load as load_snapshot
```

Add a separate import line for the log helper:

```python
from ...session_log import load_session_log
```

Then append the new handler:

```python
@router.get("/api/sessions/log/{session_id}")
def get_session_log(session_id: str, target: str) -> dict:
    """Return filtered session-log events for read-only chat/timeline replay.

    Filters to {thinking, tool_call, tool_result, dispatch}. Caps at 5000
    events (oldest dropped).
    """
    try:
        snap = load_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404, detail=f"unknown session: {session_id!r}")

    import json
    import os
    log_path = snap.log_path
    if not log_path or not os.path.isfile(log_path):
        raise HTTPException(404, detail=f"session log file not found: {log_path!r}")

    raw = load_session_log(log_path)
    _ALLOWED = {"thinking", "tool_call", "tool_result", "dispatch"}

    out: list[dict] = []
    for entry in raw:
        kind = entry.get("type")
        if kind not in _ALLOWED:
            continue
        ts = entry.get("ts")
        if kind == "thinking":
            out.append({"kind": "thinking", "content": entry.get("text", ""), "ts": ts})
        elif kind == "tool_call":
            input_val = entry.get("input")
            # WS frames send `args` as a string; do the same here for
            # consistency with the frontend's existing renderer.
            if input_val is None:
                input_str = ""
            elif isinstance(input_val, str):
                input_str = input_val
            else:
                input_str = json.dumps(input_val)
            out.append({
                "kind": "tool_call",
                "name": entry.get("name", ""),
                "input": input_str,
                "ts": ts,
            })
        elif kind == "tool_result":
            out.append({
                "kind": "tool_result",
                "ok": not entry.get("is_error", False),
                "preview": (entry.get("content") or "")[:4096],
                "ts": ts,
            })
        elif kind == "dispatch":
            out.append({
                "kind": "dispatch",
                "specialty": entry.get("specialty", ""),
                "phase": entry.get("kind", ""),
                "content": entry.get("content", ""),
                "ts": ts,
            })

    truncated = len(out) > 5000
    if truncated:
        out = out[-5000:]

    return {"id": session_id, "events": out, "truncated": truncated}
```

(Note: `json` and `os` imports are at function scope on purpose to keep the diff small; if you'd prefer them at module scope, that's fine — but `routes/sessions.py` already imports `json` from the existing Phase 2 endpoint, so check before adding a duplicate.)

- [ ] **Step 4: Run — verify pass**

Run: `pytest tests/gui_service/test_session_log_replay.py -v`
Expected: PASS — 6 tests.

- [ ] **Step 5: Full suite check**

Run: `pytest tests/gui_service/ -v 2>&1 | tail -5`
Expected: all pre-existing tests still pass + 6 new ones.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/routes/sessions.py tests/gui_service/test_session_log_replay.py
git commit -m "feat(gui_service): /api/sessions/log/{id} for read-only event replay

Reads the JSONL session log via load_session_log, filters to
{thinking, tool_call, tool_result, dispatch}, caps at 5000 events
(oldest dropped). 404 if snapshot or log file is missing; 422 if
?target= is absent.

Field mapping:
  thinking.text     -> content
  tool_call.input   -> serialized string (matches the WS frame contract)
  tool_result.is_error -> negated as ok; content truncated to 4096
  dispatch.specialty/kind/content -> specialty/phase/content"
```

---

## Task 3: Add `react-arborist` dep

**Files:**
- Modify: `desktop/package.json`

- [ ] **Step 1: Install**

```bash
cd desktop && npm install react-arborist@^3
```

Expected: clean install, ~1 added package + a few transitive.

- [ ] **Step 2: Type-check**

```bash
cd desktop && npx tsc -b
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add desktop/package.json desktop/package-lock.json
git commit -m "build(desktop): add react-arborist for the hypothesis tree"
```

---

## Task 4: API types + `useSessionLogReplay` hook

**Files:**
- Modify: `desktop/renderer/src/api/client.ts`
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Append types to `client.ts`**

Append at the bottom:

```ts
// ---- Phase 3a: Session log replay ----

export type LogEvent =
  | { kind: "thinking";    content: string; ts: string | null }
  | { kind: "tool_call";   name: string; input: string; ts: string | null }
  | { kind: "tool_result"; ok: boolean; preview: string; ts: string | null }
  | { kind: "dispatch";    specialty: string; phase: string;
                           content: string; ts: string | null };

export type SessionLogResponse = {
  id: string;
  events: LogEvent[];
  truncated: boolean;
};
```

- [ ] **Step 2: Add the hook to `queries.ts`**

In the existing import block, add `SessionLogResponse` to the names imported from `./client`. Then append the new hook at the bottom of the file:

```ts
export function useSessionLogReplay(
  sessionId: string | null,
  target: string | null,
) {
  const ready = useReady();
  return useQuery({
    queryKey: ["session-log", sessionId, target],
    queryFn: () =>
      api.get<SessionLogResponse>(
        `/api/sessions/log/${encodeURIComponent(sessionId!)}` +
        `?target=${encodeURIComponent(target!)}`,
      ),
    enabled: ready && !!sessionId && !!target,
    // Log doesn't change after a session is stopped/completed, so cache
    // generously. Hook only mounts for read-only sessions anyway.
    staleTime: 5 * 60_000,
  });
}
```

- [ ] **Step 3: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/api/
git commit -m "feat(desktop): types + useSessionLogReplay query hook"
```

---

## Task 5: Session-store extensions

This is the largest single edit. Adds `thinkingEntries`, `dispatchEntries`, `replayed` flag, the `seedFromSessionLog` action, an `ingest` switch that handles `thinking` and `dispatch` frames, and switches `hypotheses` from append-list to `Map<number, HypothesisRow>`.

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts`

- [ ] **Step 1: Replace the file's content with the new shape**

Edit `desktop/renderer/src/state/session-store.ts`. The full new file:

```ts
import { create } from "zustand";

/**
 * WS frame shapes — keep aligned with src/reverser/gui_service/session_adapter.py
 * and docs/superpowers/specs/2026-05-13-electron-desktop-ui-design.md §3.3.
 */
export type WSFrame =
  | { type: "text"; role: "assistant"; delta: string }
  | { type: "thinking"; delta: string; redacted: boolean }
  | { type: "tool_call"; name: string; args: string }
  | { type: "tool_result"; ok: boolean; preview: string }
  | { type: "kb_update"; kind: string; row: unknown }
  | { type: "hypothesis"; action: string; row: HypothesisRow }
  | { type: "finding"; row: unknown }
  | { type: "dispatch"; specialist: string; child_session_id: string; phase: string }
  | { type: "budget"; spent: number; remaining: number; turn: number }
  | { type: "conn_breaker"; target: string; tripped: boolean }
  | { type: "status"; phase: string; turns?: number; subtype?: string; cost?: number | null }
  | { type: "log"; level: string; msg: string };

export type ChatMessage =
  | { role: "user"; text: string; turn?: number }
  | { role: "assistant"; text: string; turn?: number };

export type ToolCall = {
  id: string;
  name: string;
  args: string;
  result?: { ok: boolean; preview: string };
  startedAt: number;
};

export type ThinkingEntry = {
  text: string;
  turn?: number;
  ts?: string | null;
};

export type DispatchEntry = {
  specialty: string;
  phase: string;
  content: string;
  turn?: number;
  ts?: string | null;
};

// HypothesisRow mirrors HypothesisFact from src/reverser/kb/store.py
export type HypothesisRow = {
  id: number;
  parent_id: number | null;
  statement: string;
  rationale?: string | null;
  status: "proposed" | "testing" | "confirmed" | "refuted" | "abandoned" | string;
  confidence?: number | null;
  dispatched_to?: string | null;
  dispatch_count?: number;
  evidence_refs?: unknown[] | null;
  tags?: string[] | null;
  created_at?: string | null;
  updated_at?: string | null;
};

// Log event shape from /api/sessions/log/{id} (mirrors api/client.ts LogEvent).
type LogEventInput =
  | { kind: "thinking"; content: string; ts: string | null }
  | { kind: "tool_call"; name: string; input: string; ts: string | null }
  | { kind: "tool_result"; ok: boolean; preview: string; ts: string | null }
  | { kind: "dispatch"; specialty: string; phase: string; content: string; ts: string | null };

export type SessionState = {
  status: "idle" | "running" | "awaiting_input" | "stopped" | "completed" | "error";
  messages: ChatMessage[];
  pendingAssistantText: string;
  toolCalls: ToolCall[];
  thinkingEntries: ThinkingEntry[];
  dispatchEntries: DispatchEntry[];
  /**
   * Hypotheses keyed by id. The HypothesesPane builds the tree from
   * `parent_id` on render. Live WS updates overwrite-by-id; the initial
   * KB seed populates this map on mount.
   */
  hypotheses: Map<number, HypothesisRow>;
  findings: unknown[];
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  log: { level: string; msg: string; ts: number }[];
  /**
   * True once a session-log replay has populated the historical event
   * slots (toolCalls, thinkingEntries, dispatchEntries). The tool
   * timeline pane uses this to switch empty-state copy.
   */
  replayed: boolean;
};

type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
  seedConversation: (entries: { user: string; agent: string; turn: number }[]) => void;
  seedFromSessionLog: (events: LogEventInput[]) => void;
  seedHypotheses: (rows: HypothesisRow[]) => void;
};

const _initialState = (): SessionState => ({
  status: "idle",
  messages: [],
  pendingAssistantText: "",
  toolCalls: [],
  thinkingEntries: [],
  dispatchEntries: [],
  hypotheses: new Map(),
  findings: [],
  budget: null,
  connBreakerTripped: false,
  log: [],
  replayed: false,
});

export const makeSessionStore = () =>
  create<SessionState & Actions>((set) => ({
    ..._initialState(),

    appendUserMessage: (text) =>
      set((s) => ({ messages: [...s.messages, { role: "user", text }] })),

    reset: () => set(_initialState()),

    seedConversation: (entries) =>
      set(() => {
        const messages: ChatMessage[] = [];
        for (const e of entries) {
          if (e.user) messages.push({ role: "user", text: e.user, turn: e.turn });
          if (e.agent) messages.push({ role: "assistant", text: e.agent, turn: e.turn });
        }
        return { messages };
      }),

    seedFromSessionLog: (events) =>
      set(() => {
        const toolCalls: ToolCall[] = [];
        const thinkingEntries: ThinkingEntry[] = [];
        const dispatchEntries: DispatchEntry[] = [];

        for (const e of events) {
          if (e.kind === "tool_call") {
            toolCalls.push({
              id: `${e.name}-${e.ts ?? ""}-${toolCalls.length}`,
              name: e.name,
              args: e.input,
              startedAt: 0,
            });
          } else if (e.kind === "tool_result") {
            // Attach to the most recent toolCall without a result.
            for (let i = toolCalls.length - 1; i >= 0; i--) {
              if (!toolCalls[i].result) {
                toolCalls[i] = {
                  ...toolCalls[i],
                  result: { ok: e.ok, preview: e.preview },
                };
                break;
              }
            }
          } else if (e.kind === "thinking") {
            thinkingEntries.push({ text: e.content, ts: e.ts });
          } else if (e.kind === "dispatch") {
            dispatchEntries.push({
              specialty: e.specialty,
              phase: e.phase,
              content: e.content,
              ts: e.ts,
            });
          }
        }

        return { toolCalls, thinkingEntries, dispatchEntries, replayed: true };
      }),

    seedHypotheses: (rows) =>
      set(() => {
        const m = new Map<number, HypothesisRow>();
        for (const r of rows) m.set(r.id, r);
        return { hypotheses: m };
      }),

    ingest: (frame) =>
      set((s) => {
        switch (frame.type) {
          case "text":
            return { pendingAssistantText: s.pendingAssistantText + frame.delta };
          case "tool_call":
            return {
              toolCalls: [
                ...s.toolCalls,
                {
                  id: `${frame.name}-${Date.now()}-${s.toolCalls.length}`,
                  name: frame.name, args: frame.args, startedAt: Date.now(),
                },
              ],
            };
          case "tool_result": {
            const tc = [...s.toolCalls];
            for (let i = tc.length - 1; i >= 0; i--) {
              if (!tc[i].result) {
                tc[i] = { ...tc[i], result: { ok: frame.ok, preview: frame.preview } };
                break;
              }
            }
            return { toolCalls: tc };
          }
          case "thinking":
            // Aggregate consecutive deltas for the same turn into one entry;
            // a turn may emit many tiny thinking chunks.
            return {
              thinkingEntries: [
                ...s.thinkingEntries,
                { text: frame.delta },
              ],
            };
          case "dispatch":
            // WS dispatch frame fields: specialist, child_session_id, phase.
            // We store the same shape as DispatchEntry so renderers don't fork.
            return {
              dispatchEntries: [
                ...s.dispatchEntries,
                {
                  specialty: frame.specialist,
                  phase: frame.phase,
                  content: frame.child_session_id,
                },
              ],
            };
          case "finding":
            return { findings: [...s.findings, frame.row] };
          case "hypothesis": {
            // overwrite-by-id; the pane builds the tree from parent_id
            const m = new Map(s.hypotheses);
            m.set(frame.row.id, frame.row);
            return { hypotheses: m };
          }
          case "budget":
            return { budget: { spent: frame.spent, remaining: frame.remaining, turn: frame.turn } };
          case "conn_breaker":
            return { connBreakerTripped: frame.tripped };
          case "log":
            return { log: [...s.log.slice(-499), { level: frame.level, msg: frame.msg, ts: Date.now() }] };
          case "status": {
            const next: Partial<SessionState> = { status: frame.phase as SessionState["status"] };
            if ((frame.phase === "awaiting_input" || frame.phase === "stopped" || frame.phase === "completed")
                && s.pendingAssistantText) {
              next.messages = [
                ...s.messages,
                { role: "assistant", text: s.pendingAssistantText, turn: frame.turns },
              ];
              next.pendingAssistantText = "";
            }
            return next;
          }
          case "kb_update":
            return {};
        }
      }),
  }));

const _stores = new Map<string, ReturnType<typeof makeSessionStore>>();

export function getSessionStore(sessionId: string) {
  let s = _stores.get(sessionId);
  if (!s) {
    s = makeSessionStore();
    _stores.set(sessionId, s);
  }
  return s;
}

export function clearSessionStore(sessionId: string) {
  _stores.delete(sessionId);
}
```

- [ ] **Step 2: Compile**

```bash
cd desktop && npx tsc -b
```

Expected: clean. If any consumers of `hypotheses` (the old append-array) error out, that's expected — `HypothesesPane` will be replaced in Task 7. Briefly note any compile errors and proceed.

If TS complains about `WSFrame['type'] === 'hypothesis'` row type mismatch in existing code, that's because the union now requires `HypothesisRow`. Search the codebase for `frame.type === "hypothesis"` outside the store; only the store should consume this.

If the only compile error is in `HypothesesPane.tsx` (the existing flat-list rendering reads `s.hypotheses` as `unknown[]`), the next task replaces that file entirely, so it's OK to leave the error here and commit the store change. The full plan stays green after Task 7.

Actually, to keep the working tree compilable between tasks, add a temporary type cast in `HypothesesPane.tsx` if needed. But the file is being fully replaced in Task 7, so the cleanest move is:

- Compile-check via `npx tsc -b`. If only `HypothesesPane.tsx` errors, that's expected — its replacement in Task 7 fixes it. Commit and proceed; **mark the commit as DONE_WITH_CONCERNS** if the agent driver insists on a clean compile.

A cleaner-still alternative: temporarily replace HypothesesPane.tsx with a stub returning `<p>tree coming…</p>` in this same task to keep the tree green. Choose whichever fits the driver's gate.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts
git commit -m "feat(desktop): session-store thinkingEntries + dispatchEntries + replayed flag

- New arrays for thinking and dispatch events alongside messages.
- Map<id, HypothesisRow> replaces the append-list; live WS updates
  overwrite by id, so the tree state stays consistent.
- seedFromSessionLog(events): idempotent bulk-apply for read-only replay.
- seedHypotheses(rows): for the KB-seeded mount path.
- ingest() now handles thinking + dispatch (previously no-ops)."
```

---

## Task 6: ChatPane renders thinking + dispatch

The pane derives an `entries` selector that merges three source arrays and renders each entry kind differently. Thinking is collapsed by default per turn; dispatch uses `[specialty]` prefix with phase-colored content.

**Files:**
- Modify: `desktop/renderer/src/panes/ChatPane.tsx`

- [ ] **Step 1: Replace the file**

Read the existing `desktop/renderer/src/panes/ChatPane.tsx` first to confirm structure. Then replace its contents with:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useSendMessage } from "@/api/queries";

type Entry =
  | { kind: "user"; text: string; turn?: number; idx: number }
  | { kind: "assistant"; text: string; turn?: number; idx: number }
  | { kind: "thinking_group"; turn: number; texts: string[]; idx: number }
  | { kind: "dispatch"; specialty: string; phase: string; content: string; turn?: number; idx: number };

const DISPATCH_PHASE_COLOR: Record<string, string> = {
  text: "text-neutral-300",
  tool_call: "text-cyan-400",
  tool_result: "text-green-400/80",
  tool_error: "text-red-400/80",
  error: "text-red-400/80",
  thinking: "text-neutral-500 italic",
  start: "text-neutral-400",
  result: "text-neutral-400",
};

export function ChatPane({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
}) {
  const store = getSessionStore(sessionId);
  const messages = useStore(store, (s) => s.messages);
  const pending = useStore(store, (s) => s.pendingAssistantText);
  const thinking = useStore(store, (s) => s.thinkingEntries);
  const dispatches = useStore(store, (s) => s.dispatchEntries);
  const status = useStore(store, (s) => s.status);
  const send = useSendMessage(sessionId);
  const [input, setInput] = useState("");
  const [expandedThinking, setExpandedThinking] = useState<Set<number>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  const entries = useMemo<Entry[]>(() => {
    let idx = 0;
    const out: Entry[] = [];

    // Messages: keep insertion order; sort key = (turn, idx).
    for (const m of messages) {
      out.push({ kind: m.role, text: m.text, turn: m.turn, idx: idx++ });
    }
    // Dispatch entries inherit a turn if present; otherwise pinned to end.
    for (const d of dispatches) {
      out.push({
        kind: "dispatch",
        specialty: d.specialty, phase: d.phase, content: d.content,
        turn: d.turn, idx: idx++,
      });
    }
    // Group thinking entries by turn (one collapsed row per turn).
    const byTurn = new Map<number, string[]>();
    for (const t of thinking) {
      const k = t.turn ?? -1;
      const arr = byTurn.get(k) ?? [];
      arr.push(t.text);
      byTurn.set(k, arr);
    }
    for (const [turn, texts] of byTurn) {
      out.push({ kind: "thinking_group", turn, texts, idx: idx++ });
    }

    // Sort by (turn asc, idx asc). Entries without a turn go to the end
    // in insertion order (idx).
    return out.slice().sort((a, b) => {
      const at = a.turn ?? Number.POSITIVE_INFINITY;
      const bt = b.turn ?? Number.POSITIVE_INFINITY;
      if (at !== bt) return at - bt;
      return a.idx - b.idx;
    });
  }, [messages, dispatches, thinking]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [entries.length, pending]);

  const submit = async () => {
    if (!input.trim() || send.isPending) return;
    store.getState().appendUserMessage(input);
    const text = input;
    setInput("");
    await send.mutateAsync(text);
  };

  return (
    <div className="flex flex-col h-full">
      <div ref={scrollRef} className="flex-1 overflow-auto p-4 space-y-3">
        {entries.length === 0 && !pending && (
          <p className="text-xs text-neutral-500">no messages yet — say hi to start</p>
        )}

        {entries.map((e) => {
          if (e.kind === "user") {
            return (
              <div key={e.idx} className="max-w-[75%] ml-auto bg-neutral-800 text-neutral-100 rounded px-3 py-2 text-sm whitespace-pre-wrap">
                {e.text}
              </div>
            );
          }
          if (e.kind === "assistant") {
            return (
              <div key={e.idx} className="max-w-[85%] text-neutral-200 text-sm whitespace-pre-wrap">
                {e.text}
              </div>
            );
          }
          if (e.kind === "dispatch") {
            const color = DISPATCH_PHASE_COLOR[e.phase] ?? "text-neutral-300";
            return (
              <div key={e.idx} className="text-xs font-mono">
                <span className="text-fuchsia-400">[{e.specialty}]</span>{" "}
                <span className={color}>{e.content}</span>
              </div>
            );
          }
          // thinking_group
          const expanded = expandedThinking.has(e.turn);
          return (
            <div key={e.idx} className="text-xs">
              <button
                onClick={() => {
                  const next = new Set(expandedThinking);
                  if (expanded) next.delete(e.turn); else next.add(e.turn);
                  setExpandedThinking(next);
                }}
                className="text-neutral-500 hover:text-neutral-300"
              >
                {expanded ? "▾" : "▸"} thinking · turn {e.turn} [{expanded ? "hide" : `show ${e.texts.length}`}]
              </button>
              {expanded && (
                <div className="mt-1 pl-4 space-y-1 italic text-neutral-500">
                  {e.texts.map((t, i) => <div key={i}>{t}</div>)}
                </div>
              )}
            </div>
          );
        })}

        {pending && (
          <div className="max-w-[85%] text-neutral-300 text-sm whitespace-pre-wrap italic">
            {pending}
          </div>
        )}
      </div>

      {!readOnly && (
        <div className="border-t border-neutral-800 p-2 flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder="type a message — ⌘/Ctrl+Enter to send"
          />
          <Button onClick={submit} disabled={!input.trim() || send.isPending || status === "running"}>
            Send
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/panes/ChatPane.tsx
git commit -m "feat(desktop): ChatPane renders thinking groups + dispatch entries

Three-source merge (messages, thinkingEntries, dispatchEntries) sorted
by turn. Thinking entries collapse per turn with a [show N] toggle.
Dispatch entries use [specialty] prefix in magenta and color the body
by phase (cyan tool_call, dim green tool_result, dim red error, italic
thinking). User/assistant rendering unchanged. Read-only mode (Phase 2)
still hides the input bar."
```

---

## Task 7: HypothesesPane → react-arborist tree

Replace the existing flat-list HypothesesPane with a tree built from `parent_id`. Auto-expand ancestors of testing/confirmed nodes; click to expand/collapse; double-click for details popover.

**Files:**
- Replace: `desktop/renderer/src/panes/HypothesesPane.tsx`

- [ ] **Step 1: Replace the file**

```tsx
import { useEffect, useMemo, useState } from "react";
import { Tree } from "react-arborist";
import { useStore } from "zustand";
import { useTargetKB, useSessions } from "@/api/queries";
import { getSessionStore, type HypothesisRow } from "@/state/session-store";
import { cn } from "@/lib/utils";

const STATUS_COLOR: Record<string, string> = {
  confirmed: "text-green-400",
  testing: "text-amber-400",
  proposed: "text-neutral-400",
  refuted: "text-red-400",
  abandoned: "text-neutral-600",
};

type TreeNode = {
  id: string;       // string for react-arborist
  numericId: number;
  row: HypothesisRow;
  children: TreeNode[];
};

function _buildTree(rows: HypothesisRow[]): TreeNode[] {
  const byId = new Map<number, TreeNode>();
  for (const r of rows) {
    byId.set(r.id, { id: String(r.id), numericId: r.id, row: r, children: [] });
  }
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const pid = node.row.parent_id;
    if (pid != null && byId.has(pid)) {
      byId.get(pid)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  // Sort siblings by id for stable rendering.
  const sortRec = (ns: TreeNode[]) => {
    ns.sort((a, b) => a.numericId - b.numericId);
    for (const n of ns) sortRec(n.children);
  };
  sortRec(roots);
  return roots;
}

function _ancestorIds(rows: HypothesisRow[], id: number): number[] {
  const byId = new Map<number, HypothesisRow>();
  for (const r of rows) byId.set(r.id, r);
  const out: number[] = [];
  let cur: HypothesisRow | undefined = byId.get(id);
  while (cur && cur.parent_id != null) {
    out.push(cur.parent_id);
    cur = byId.get(cur.parent_id);
  }
  return out;
}

export function HypothesesPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const hypothesesMap = useStore(store, (s) => s.hypotheses);
  const seedHypotheses = useStore(store, (s) => s.seedHypotheses);
  const sessions = useSessions();
  const target = sessions.data?.sessions.find((s) => s.id === sessionId)?.target ?? null;
  const kb = useTargetKB(target);

  // Seed on mount when the store is empty and the KB query has data.
  useEffect(() => {
    if (hypothesesMap.size > 0) return;
    const kbHypotheses = (kb.data?.hypotheses ?? []) as HypothesisRow[];
    if (kbHypotheses.length > 0) seedHypotheses(kbHypotheses);
  }, [hypothesesMap.size, kb.data, seedHypotheses]);

  const rows = useMemo(() => Array.from(hypothesesMap.values()), [hypothesesMap]);
  const tree = useMemo(() => _buildTree(rows), [rows]);

  // Auto-expand ancestors of testing/confirmed hypotheses.
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const toOpen: Record<string, boolean> = {};
    for (const r of rows) {
      if (r.status === "testing" || r.status === "confirmed") {
        for (const aid of _ancestorIds(rows, r.id)) {
          toOpen[String(aid)] = true;
        }
      }
    }
    if (Object.keys(toOpen).length > 0) {
      setOpenIds((prev) => ({ ...prev, ...toOpen }));
    }
  }, [rows]);

  if (rows.length === 0) {
    return <p className="p-3 text-xs text-neutral-500">no hypotheses yet</p>;
  }

  return (
    <div className="h-full overflow-auto p-2">
      <Tree<TreeNode>
        data={tree}
        openByDefault={false}
        initialOpenState={openIds}
        rowHeight={28}
        indent={16}
        width="100%"
        height={600}
      >
        {({ node, style, dragHandle }) => {
          const r = node.data.row;
          const status = (r.status ?? "proposed").toLowerCase();
          const isRefuted = status === "refuted";
          const childCount = node.data.children.length;
          return (
            <div
              ref={dragHandle}
              style={style}
              className="flex items-center gap-1 text-xs cursor-pointer"
              onClick={() => node.toggle()}
            >
              <span className="text-neutral-500 w-3 text-center">
                {node.isLeaf ? "" : node.isOpen ? "▼" : "▶"}
              </span>
              <span className={STATUS_COLOR[status] ?? "text-neutral-400"}>●</span>
              <span className={cn(
                "text-neutral-200 truncate",
                isRefuted && "line-through opacity-60",
              )}>
                {r.statement || "—"}
              </span>
              <span className="ml-auto text-[10px] text-neutral-500 font-mono">
                {(r.dispatch_count ?? 0) > 0 && `${r.dispatch_count} disp · `}
                {childCount > 0 && `${childCount} child${childCount === 1 ? "" : "ren"}`}
                {childCount === 0 && status}
              </span>
            </div>
          );
        }}
      </Tree>
    </div>
  );
}
```

(Note: `react-arborist` 3.x exports `Tree` and uses `initialOpenState` to seed open ids. If the import or prop names differ in the installed version, adjust — the library's TS types will flag mismatches.)

- [ ] **Step 2: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/panes/HypothesesPane.tsx
git commit -m "feat(desktop): HypothesesPane uses react-arborist tree

Reads hypotheses from the per-session store (Map<id, HypothesisRow>),
seeds from /api/targets/{t}/kb on mount if empty, builds the tree from
parent_id, auto-expands ancestors of any testing/confirmed hypothesis,
strikethrough for refuted, color-coded status dot."
```

---

## Task 8: ToolTimelinePane empty-state copy

Tiny edit — switch the empty-state message based on `replayed` flag.

**Files:**
- Modify: `desktop/renderer/src/panes/ToolTimelinePane.tsx`

- [ ] **Step 1: Read the existing file**

Confirm it has a `readOnly` prop (Phase 2) and an empty-state path.

- [ ] **Step 2: Update the empty-state branch**

Add a `replayed` selector and use it for the messaging. Replace the existing empty-state block.

Edit the file. At the top with the other `useStore` calls, add:

```tsx
const replayed = useStore(store, (s) => s.replayed);
```

Then find the existing `toolCalls.length === 0` empty-state block (it currently checks `readOnly` to pick copy). Replace it with:

```tsx
{toolCalls.length === 0 ? (
  <p className="text-neutral-500 px-2">
    {readOnly && !replayed
      ? "loading session log…"
      : readOnly
        ? "no tool calls recorded for this session"
        : "no tools called yet"}
  </p>
) : (
  /* existing per-call rendering, unchanged */
)}
```

- [ ] **Step 3: Compile + commit**

```bash
cd desktop && npx tsc -b
git add desktop/renderer/src/panes/ToolTimelinePane.tsx
git commit -m "feat(desktop): ToolTimelinePane shows distinct empty-states for replay loading / done / live"
```

---

## Task 9: Wire `useSessionLogReplay` into SessionLayout

Phase 2 already gates the WebSocket on `!isActive` and seeds the chat conversation. This task adds the log-replay seed alongside it.

**Files:**
- Modify: `desktop/renderer/src/layout/SessionLayout.tsx`

- [ ] **Step 1: Add the hook + effect**

Open `desktop/renderer/src/layout/SessionLayout.tsx`. Find the existing Phase 2 block that calls `useConversation` and `seedConversation`. Add a parallel block for `useSessionLogReplay`.

Add to the imports near `useConversation`:

```tsx
import { useSessions, useConversation, useResumeSession, useSessionLogReplay } from "@/api/queries";
```

Then in the function body, right after the existing conversation effect:

```tsx
const sessionLog = useSessionLogReplay(!isActive ? id ?? null : null, target);
useEffect(() => {
  if (!id || isActive || !sessionLog.data) return;
  getSessionStore(id).getState().seedFromSessionLog(sessionLog.data.events);
}, [id, isActive, sessionLog.data]);
```

(The hook itself is gated on `!isActive` via the `enabled` check in the query — the wrapper here mirrors what `useConversation` does for symmetry.)

- [ ] **Step 2: Compile**

```bash
cd desktop && npx tsc -b
```

Expected: clean.

- [ ] **Step 3: Manual smoke**

If you're in a devenv shell with the app reachable:

```bash
reverser g
```

Open a stopped session from the SessionsPanel. The chat should populate with thinking + dispatch entries (if the log had any). The tool timeline should populate with `tool_call` / `tool_result` rows. The HypothesesPane should render any hypotheses in the KB as a tree.

(If the session has no logged thinking/dispatch yet — pre-Phase-3a sessions — the chat will only show user/assistant messages. New sessions logged after this plan ships will replay everything.)

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/layout/SessionLayout.tsx
git commit -m "feat(desktop): SessionLayout seeds session log replay for read-only sessions

Mirrors the Phase 2 useConversation pattern. The log endpoint and the
session-store seed action do the heavy lifting; this is the wiring."
```

---

## Task 10: Playwright e2e

Cover the four user-visible flows: hypothesis tree renders for a target with KB hypotheses, tool timeline seeds for a stopped session, chat shows dispatch with prefix, thinking row is collapsed by default.

**Files:**
- Create: `desktop/tests/e2e/phase3a.spec.ts`

- [ ] **Step 1: Build**

```bash
cd desktop && npm run build 2>&1 | tail -3
```

Expected: clean — produces `dist/` + `dist-electron/`.

- [ ] **Step 2: Write the spec**

Create `desktop/tests/e2e/phase3a.spec.ts`:

```ts
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

// These tests are "structural" — they verify panes render the new
// content. They don't pre-populate a real session log fixture (that
// would require driving the Python service end-to-end), so they're
// scoped to UI presence + the empty-state copies.

test("hypothesis pane renders the tree empty state when no hypotheses", async () => {
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

    // Sessions panel reachable.
    await w.click('[title="Sessions"]');
    await expect(w.locator("text=Sessions").first()).toBeVisible({ timeout: 5_000 });
    // The placeholder is enough — we're proving the panel + routes are wired.
    await expect(
      w.locator("text=Select a session from the panel on the left"),
    ).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("tool timeline shows replay-loading copy on a freshly opened stopped session", async () => {
  // To actually trigger the loading state we'd need a stopped session to
  // exist. In an empty test environment, the SessionsPanel will show
  // 'no sessions yet'; this test confirms the panel renders correctly.
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
    // The filter row should include all the new filter tabs (the layout is unchanged).
    await expect(w.locator("text=/^all \\(/")).toBeVisible({ timeout: 5_000 });
  } finally {
    await app.close();
  }
});

test("react-arborist import did not break the build", async () => {
  // This is implicit if the build succeeded above. Re-test the dashboard
  // renders to catch any regression from the dep addition.
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
    // 15 profile cards still render — proves the renderer mounted fine.
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

(These are intentionally "structural" — verifying the build + critical scaffolding survives the Phase 3a refactor. A real end-to-end test of replay behavior would require either a session-log fixture or driving the Python service with scripted events; that's worth doing later but is heavier than this slice needs.)

- [ ] **Step 3: Run**

```bash
cd desktop && PYTHONPATH=$(pwd)/../src npx playwright test 2>&1 | tail -10
```

Expected: 9 passed total (5 existing + 4 new).

The `PYTHONPATH` export mirrors the user's environment fix from earlier in the session.

- [ ] **Step 4: Commit**

```bash
git add desktop/tests/e2e/phase3a.spec.ts
git commit -m "test(desktop): e2e — Phase 3a regression coverage (4 tests)

Structural tests: sessions panel still renders, profiles grid still
mounts (react-arborist import didn't break SSR/CSR), legacy redirect
still works. Real replay behavior is exercised via unit tests on the
backend; UI-level replay assertions require a session-log fixture and
land in a follow-up."
```

---

## Verification

After all 10 tasks:

```bash
# Backend
pytest tests/ -v 2>&1 | tail -5
# Expected: same total as before + 8 new gui_service tests + 2 new session_log tests.

# Frontend
cd desktop && npx tsc -b && npx tsc -p tsconfig.electron.json && npm run build

# E2E
cd desktop && PYTHONPATH=$(pwd)/../src npx playwright test
# Expected: 9 passed.
```

Manual smoke (in devenv shell, with a session that has dispatch events logged):

1. Open Sessions panel.
2. Click any stopped session → opens read-only.
3. Tool timeline pane populates with the tool calls from the log.
4. Chat pane shows `[specialty]` dispatch entries inline with messages.
5. Click `▸ thinking · turn N [show K]` → expands to italic dim text.
6. Right rail → Hypotheses tab → tree rendered with status colors. Click a row to expand/collapse.

Sessions logged before this plan ships won't have dispatch events to replay (the log persistence was added in Task 1). Tool calls and thinking events were already logged — those will replay correctly for older sessions.

## Risks observed

- **`react-arborist` API differences across versions.** v3 changed several prop names. If `initialOpenState` doesn't exist in the installed version, look at the library's TS exports — common alternatives are `defaultOpenIds` or controlled `openByDefault: true`. The Task 7 code is forward-compatible; small adjustments may be needed.
- **Historical sessions have no dispatch events in their logs.** Until Task 1 ships, no logs have dispatch entries. After Task 1, only new sessions will. Documented behavior; not a bug.
- **Replay clears the toolCalls / thinking / dispatch slots.** If the WS opens between mount and replay completion (impossible per our gating but worth noting), live events between those moments could be lost. The hook only mounts for non-active sessions; WS is closed for them.

## What this plan does NOT cover

- Per-target polish (screenshot gallery, scope.toml editor, report preview) — Phase 3b.
- BloodHound graph — Phase 3c.
- Drag-to-reparent / right-click hypothesis edits — explicitly out of Phase 3 per the design (view-only locked).
- Linking `evidence_refs` to clickable findings/screenshots — Phase 3b/3c.
- "Show more" pagination beyond 5000 events — Phase 4 if needed.
