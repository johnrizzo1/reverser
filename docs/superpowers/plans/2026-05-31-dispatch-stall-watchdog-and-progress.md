# Dispatch Stall Watchdog + Sub-Agent Progress Visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a hung dispatched specialist from wedging the whole session on "running" forever, and make sub-agent progress/staleness legible in the UI.

**Architecture:** Backend wraps the specialist's event generator with an idle-timeout watchdog that raises `_DispatchStalled` when no event arrives within a configurable window; the existing `finally` then emits `end`, clears `in_flight`, and returns a partial report. The frontend stamps each dispatch frame with a receive time and shows an idle/stalled indicator on the DispatchPanel and a live active-dispatch chip in the status bar. Dispatch `start`/`end` are also persisted to the JSONL session log so replay renders them (the seeder already consumes them).

**Tech Stack:** Python 3 / asyncio / pytest (`asyncio_mode = "auto"`); React + Zustand / TypeScript / vitest.

Spec: `docs/superpowers/specs/2026-05-31-dispatch-stall-watchdog-and-progress-design.md`

---

### Task 1: Idle-timeout watchdog primitive (backend, pure)

**Files:**
- Modify: `src/reverser/tools/dispatch.py` (add helpers after `_unserialized_dispatch_slot`, ~line 385)
- Test: `tests/test_dispatch_watchdog.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_watchdog.py`:

```python
"""Tests for the dispatch idle-timeout watchdog primitive."""
import asyncio
import pytest

from reverser.tools.dispatch import (
    _DispatchStalled,
    _aiter_with_idle_timeout,
    _dispatch_idle_timeout,
)


async def _collect(agen):
    out = []
    async for item in agen:
        out.append(item)
    return out


async def test_passes_items_through_when_fast():
    async def fast():
        for i in range(3):
            yield i
    result = await _collect(_aiter_with_idle_timeout(fast(), 1.0))
    assert result == [0, 1, 2]


async def test_raises_stalled_when_idle_exceeds_window():
    closed = {"v": False}

    async def stalls():
        yield 0
        await asyncio.sleep(5)   # longer than the idle window
        yield 1                  # never reached

    agen = stalls()
    # wrap so we can observe aclose(); _aiter_with_idle_timeout closes the
    # *underlying* iterator, so assert via the sleep not hanging the test.
    with pytest.raises(_DispatchStalled):
        await _collect(_aiter_with_idle_timeout(agen, 0.2))


def test_idle_timeout_reads_env(monkeypatch):
    monkeypatch.delenv("REVERSER_DISPATCH_IDLE_TIMEOUT", raising=False)
    assert _dispatch_idle_timeout() == 300.0
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "12.5")
    assert _dispatch_idle_timeout() == 12.5
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "garbage")
    assert _dispatch_idle_timeout() == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dispatch_watchdog.py -v`
Expected: FAIL with `ImportError: cannot import name '_DispatchStalled'`.

- [ ] **Step 3: Write minimal implementation**

In `src/reverser/tools/dispatch.py`, after the `_unserialized_dispatch_slot` definition (~line 385), add:

```python
import os as _os  # noqa: E402  (module already imports asyncio at top)


class _DispatchStalled(Exception):
    """Raised when a dispatched specialist emits no event within the idle window."""


def _dispatch_idle_timeout() -> float:
    """Seconds of sub-agent silence before the stall watchdog aborts a dispatch.

    Default 300s (5 min); override with REVERSER_DISPATCH_IDLE_TIMEOUT. A
    malformed value falls back to the default rather than crashing a dispatch.
    """
    raw = _os.environ.get("REVERSER_DISPATCH_IDLE_TIMEOUT")
    if raw is None:
        return 300.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 300.0


async def _aiter_with_idle_timeout(agen, idle_seconds: float):
    """Yield from ``agen``, raising ``_DispatchStalled`` if any single step
    idles longer than ``idle_seconds``. Best-effort closes the underlying
    iterator on stall so the specialist subprocess/generator is not leaked."""
    it = agen.__aiter__()
    while True:
        try:
            item = await asyncio.wait_for(it.__anext__(), idle_seconds)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            try:
                await it.aclose()
            except Exception:
                pass
            raise _DispatchStalled(idle_seconds)
        yield item
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dispatch_watchdog.py -v`
Expected: PASS (3 tests). The stall test completes in ~0.2s, not 5s.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch_watchdog.py
git commit -m "feat(dispatch): idle-timeout watchdog primitive for stalled specialists"
```

---

### Task 2: Wire watchdog into `_run_specialist` + timeout status

**Files:**
- Modify: `src/reverser/tools/dispatch.py` — `_run_specialist` loops (~line 645 and ~line 692) and the dispatch body try/except (~line 741-773)
- Test: `tests/test_dispatch_watchdog.py` (append integration test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dispatch_watchdog.py`:

```python
from unittest.mock import patch


def _call_tool(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def test_dispatch_times_out_on_stalled_specialist(monkeypatch, tmp_path):
    """A specialist whose generator stalls aborts with Status: timeout,
    clears in_flight, and returns a partial report instead of hanging."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "0.3")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def stalling_query(prompt, options):
        from claude_agent_sdk import AssistantMessage, TextBlock
        yield AssistantMessage(
            content=[TextBlock(text="Partial recon so far...")], model="claude",
        )
        await asyncio.sleep(5)   # never reaches a ResultMessage

    with patch("reverser.tools.dispatch.query", stalling_query):
        result = _call_tool(dispatch_specialist, {
            "specialty": "webrecon", "sub_goal": "enumerate",
            "target": "10.10.10.5", "hypothesis_id": 1,
        })

    body = result["content"][0]["text"] if isinstance(result, dict) and "content" in result else str(result)
    assert "timeout" in body.lower()
    assert "Partial recon so far" in body          # partial report preserved
    assert sess._snapshot.in_flight is None         # finally ran
```

> Note: confirm the envelope shape by reading what `dispatch_specialist` returns near dispatch.py end (the `summary_lines` join). If it returns a raw string, assert on that string directly. Adjust the `body` extraction line to match.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dispatch_watchdog.py::test_dispatch_times_out_on_stalled_specialist -v`
Expected: FAIL — without the wrapper the test hangs ~5s then the assertion on `"timeout"` fails (status would be `error`/`success`, not `timeout`).

- [ ] **Step 3: Wire the wrapper into both loops**

In `_run_specialist`, capture the idle timeout once near the top of the function (just after the `nonlocal` line, ~line 627):

```python
        _idle = _dispatch_idle_timeout()
```

Change the session-backend loop (currently `async for event in backend.run(...)`, ~line 645):

```python
                async for event in _aiter_with_idle_timeout(
                    backend.run(
                        prompt=prompt,
                        system_prompt=full_system_prompt,
                        max_turns=max_turns,
                        max_budget_usd=budget_usd,
                        allowed_tools=sub_allowed_tools,
                    ),
                    _idle,
                ):
```

Change the claude-SDK loop (currently `async for message in query(prompt=prompt, options=options)`, ~line 692):

```python
            async for message in _aiter_with_idle_timeout(
                query(prompt=prompt, options=options), _idle,
            ):
```

> The local-backend slot acquisition stays OUTSIDE the wrapper: only the
> `backend.run(...)` iteration is wrapped, so a dispatch waiting for a local
> model slot never trips the watchdog.

- [ ] **Step 4: Handle `_DispatchStalled` as a timeout in the dispatch body**

In the dispatch body, the existing `try`/`except Exception`/`finally` is around line 741-781. Insert a dedicated `except _DispatchStalled` BEFORE the generic `except Exception` (so timeout is not relabeled as a generic error):

```python
    except _DispatchStalled as e:
        status = "timeout"
        idle_s = int(e.args[0]) if e.args else 0
        error_msg = (
            f"specialist produced no output for {idle_s}s — "
            f"aborted by stall watchdog"
        )
        if not report_text:
            report_text = f"(dispatch aborted: {error_msg})"
        outcome = "inconclusive"
        report_model = None
    except Exception as e:
        # ...unchanged...
```

The existing `finally` already calls `_emit_end(status, ...)` and clears `in_flight`, and the summary block already prints `**Status:** {status}` — so `timeout` flows through with no further change.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_dispatch_watchdog.py -v`
Expected: PASS (4 tests). The integration test finishes in well under 2s.

- [ ] **Step 6: Run the existing dispatch suite for regressions**

Run: `python -m pytest tests/test_dispatch.py tests/test_dispatch_in_flight.py tests/test_dispatch_event_callback.py -q`
Expected: PASS (no regressions — happy-path dispatches still yield a ResultMessage well within the idle window).

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch_watchdog.py
git commit -m "feat(dispatch): abort stalled specialist with timeout status, unblock session"
```

---

### Task 3: Persist dispatch start/end to the session log

**Files:**
- Modify: `src/reverser/tools/dispatch.py` — `_emit_start` (~line 561) and `_emit_end` (~line 575)
- Test: `tests/test_session_log_dispatch.py` (append)

- [ ] **Step 1: Write the failing test**

First read `tests/test_session_log_dispatch.py` to match its existing setup/fixtures, then append a test in the same style. The test must: build a `SessionLog` pointed at a tmp file (reuse the file's existing helper/fixture if present), exercise a dispatch whose start/end are emitted, and assert the JSONL contains a `{"type":"dispatch","kind":"start"}` and `{"type":"dispatch","kind":"end"}` record. If the existing file already constructs a session + patches `query`, mirror that; otherwise assert at the `_emit_start`/`_emit_end` level by checking `sess._slog` output after a patched dispatch. Concretely:

```python
def test_dispatch_start_and_end_persisted_to_log(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import json
    import asyncio
    from unittest.mock import patch
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def ok_query(prompt, options):
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")], model="claude")
        yield ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0,
                             is_error=False, num_turns=1, session_id="t",
                             total_cost_usd=0.0, result="x")

    with patch("reverser.tools.dispatch.query", ok_query):
        asyncio.new_event_loop().run_until_complete(
            (getattr(dispatch_specialist, "handler", None) or dispatch_specialist)(
                {"specialty": "ad", "sub_goal": "s", "target": "10.10.10.5", "hypothesis_id": 1}
            )
        )
    sess._slog._f.flush()
    records = [json.loads(l) for l in open(sess._slog.path) if l.strip()]
    kinds = [(r.get("type"), r.get("kind")) for r in records]
    assert ("dispatch", "start") in kinds
    assert ("dispatch", "end") in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_log_dispatch.py::test_dispatch_start_and_end_persisted_to_log -v`
Expected: FAIL — `("dispatch","start")` not in `kinds` (start/end currently go only to the WS bus).

- [ ] **Step 3: Add log persistence to `_emit_start` / `_emit_end`**

In `_emit_start` (~line 561), add a `_slog` write alongside the existing `emit_dispatch_event` call:

```python
    def _emit_start() -> None:
        if sess is None:
            return
        payload = _json.dumps({"hypothesis_id": hypothesis_id, "sub_goal": sub_goal})
        try:
            sess._slog.log_dispatch_event(
                specialty, "start", payload, dispatch_id=dispatch_id, sub_turn=0,
            )
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(specialty, dispatch_id, 0, "start", payload)
        except Exception:
            pass
```

In `_emit_end` (~line 575), the same pattern:

```python
    def _emit_end(status: str, cost: float, turns_consumed: int) -> None:
        if sess is None:
            return
        payload = _json.dumps({"status": status, "cost": cost, "turns": turns_consumed})
        try:
            sess._slog.log_dispatch_event(
                specialty, "end", payload, dispatch_id=dispatch_id, sub_turn=0,
            )
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(specialty, dispatch_id, 0, "end", payload)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_session_log_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_session_log_dispatch.py
git commit -m "feat(dispatch): persist start/end to session log so replay renders dispatch status"
```

---

### Task 4: Store — `lastActivityAt` stamping + active-dispatch selector (frontend)

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts` — `Dispatch` type (line 49), `ingest` `"dispatch"` case (lines 316-374), add `selectActiveDispatch` export
- Test: `desktop/renderer/src/state/session-store.test.ts` (append)

- [ ] **Step 1: Write the failing test**

Append to `desktop/renderer/src/state/session-store.test.ts`:

```ts
import { selectActiveDispatch } from "./session-store";

describe("dispatch lastActivityAt + active selector", () => {
  it("stamps lastActivityAt on start and updates it on sub-turn frames", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 } as never);
    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webrecon", sub_goal: "enumerate",
    });
    const d0 = store.getState().turns.get(1)!.dispatches.get("d1")!;
    expect(typeof d0.lastActivityAt).toBe("number");

    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 3,
      phase: "text", specialty: "webrecon", content: "found /admin",
    });
    const d1 = store.getState().turns.get(1)!.dispatches.get("d1")!;
    expect(d1.lastActivityAt).toBeGreaterThanOrEqual(d0.lastActivityAt!);
  });

  it("selectActiveDispatch returns the running dispatch in the current turn, else null", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 } as never);
    expect(selectActiveDispatch(store.getState())).toBeNull();

    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webrecon", sub_goal: "enumerate",
    });
    expect(selectActiveDispatch(store.getState())?.specialty).toBe("webrecon");

    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, phase: "end",
      specialty: "webrecon", status: "completed", cost: 0.1, turns: 4,
    });
    expect(selectActiveDispatch(store.getState())).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && npm test -- session-store`
Expected: FAIL — `selectActiveDispatch` is not exported / `lastActivityAt` undefined.

- [ ] **Step 3: Add the field, stamping, and selector**

In `session-store.ts`, add to the `Dispatch` type (after line 56 `turnsConsumed?: number;`):

```ts
  lastActivityAt?: number;             // Date.now() of the last live frame (live WS only)
```

In the `ingest` `"dispatch"` case, stamp on every branch. In the `"start"` branch object (line 322-329) add `lastActivityAt: Date.now(),`. In the `"end"` branch update (line 341-346) add `lastActivityAt: Date.now(),`. In the sub-turn branch's final `dispatches.set` (line 371) change to:

```ts
            dispatches.set(frame.dispatch_id, { ...d, subTurns, lastActivityAt: Date.now() });
```

At the end of the file (module scope, after the store factory), add the selector:

```ts
/** The running dispatch in the current turn, if any (reference-stable). */
export function selectActiveDispatch(s: SessionState): Dispatch | null {
  const t = s.turns.get(s.currentTurn);
  if (!t) return null;
  for (const d of t.dispatches.values()) {
    if (d.status === "running") return d;
  }
  return null;
}
```

> Returning the `Dispatch` reference (not a freshly-built object) keeps the
> Zustand selector stable across renders — no infinite re-render.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd desktop && npm test -- session-store`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(ui): stamp dispatch lastActivityAt + selectActiveDispatch selector"
```

---

### Task 5: `useNow` hook + DispatchPanel idle/stalled indicator

**Files:**
- Create: `desktop/renderer/src/hooks/useNow.ts`
- Modify: `desktop/renderer/src/panes/DispatchPanel.tsx`
- Test: `desktop/renderer/src/panes/dispatch-panel.test.tsx` (create)

- [ ] **Step 1: Write the `useNow` hook (no test needed — trivial, covered via panel test)**

Create `desktop/renderer/src/hooks/useNow.ts`:

```ts
import { useEffect, useState } from "react";

/** Re-renders the caller every `intervalMs` and returns the current epoch ms.
 *  Used to recompute "idle for N min" displays without a new data frame. */
export function useNow(intervalMs = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
```

- [ ] **Step 2: Write the failing test**

Create `desktop/renderer/src/panes/dispatch-panel.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DispatchPanel } from "./DispatchPanel";
import type { Dispatch } from "@/state/session-store";

function makeRunningDispatch(lastActivityAt: number): Dispatch {
  return {
    id: "d1", specialty: "webrecon", subGoal: "enumerate",
    status: "running", subTurns: new Map(), lastActivityAt,
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("DispatchPanel staleness", () => {
  it("shows no idle marker when recently active", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    render(<DispatchPanel dispatch={makeRunningDispatch(Date.now() - 10_000)} />);
    expect(screen.queryByText(/idle/i)).toBeNull();
  });

  it("shows an idle marker after 90s of no activity", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    render(<DispatchPanel dispatch={makeRunningDispatch(Date.now() - 120_000)} />);
    expect(screen.getByText(/idle/i)).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd desktop && npm test -- dispatch-panel`
Expected: FAIL — second test finds no `idle` text (no staleness UI yet).

- [ ] **Step 4: Add idle/stalled rendering to DispatchPanel**

In `DispatchPanel.tsx`, add imports at top:

```tsx
import { AlertTriangle } from "lucide-react";
import { useNow } from "@/hooks/useNow";
```

Define a threshold constant near the top of the file (after imports):

```tsx
const IDLE_STALE_MS = 90_000;
```

Inside `DispatchPanel`, after `const activity = latestActivity(dispatch);`, compute idle state:

```tsx
  const now = useNow(15_000);
  const idleMs =
    dispatch.status === "running" && dispatch.lastActivityAt
      ? now - dispatch.lastActivityAt
      : 0;
  const isStale = idleMs > IDLE_STALE_MS;
  const idleLabel = isStale ? `idle ${Math.round(idleMs / 60_000)}m` : null;
```

Change `StatusIcon` selection so a stale running dispatch shows a non-spinning warning icon instead of the spinner (replace the existing `StatusIcon` assignment, lines 44-47):

```tsx
  const StatusIcon = dispatch.status === "completed" ? CheckCircle2
    : dispatch.status === "error" ? XCircle
    : isStale ? AlertTriangle
    : activity?.label === "queued on local backend" ? Clock3
      : Loader2;
```

Stop the spin when stale: in the `StatusIcon` `className` `cn(...)` (line 58-64), change the spin condition to also require not-stale:

```tsx
            dispatch.status === "running" && !isStale && activity?.label !== "queued on local backend" && "animate-spin",
```

Append the idle label to the activity line (line 71). Replace that `{activity && ...}` span with:

```tsx
          {activity && <span className="ml-2 text-neutral-500">· {activity.label}</span>}
          {idleLabel && <span className="ml-2 text-amber-400">· {idleLabel}</span>}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd desktop && npm test -- dispatch-panel`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/hooks/useNow.ts desktop/renderer/src/panes/DispatchPanel.tsx desktop/renderer/src/panes/dispatch-panel.test.tsx
git commit -m "feat(ui): DispatchPanel idle/stalled indicator with useNow tick"
```

---

### Task 6: SessionStatusBar active-dispatch chip

**Files:**
- Modify: `desktop/renderer/src/layout/SessionStatusBar.tsx`
- Test: `desktop/renderer/src/layout/status-bar.test.tsx` (append)

- [ ] **Step 1: Write the failing test**

First read `desktop/renderer/src/layout/status-bar.test.tsx` to match how it mounts `SessionStatusBar` (it likely seeds a store via `getSessionStore(id)` and wraps with query-client/providers). Append a test that mirrors that setup and asserts the chip appears. Shape:

```tsx
it("shows the active dispatch chip while a specialist is running", () => {
  // ...reuse the file's existing render helper + sessionId...
  const store = getSessionStore(sessionId);
  store.getState().ingest({ type: "status", phase: "running", turns: 1 } as never);
  store.getState().ingest({
    type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
    specialty: "webrecon", sub_goal: "enumerate",
  });
  store.getState().ingest({
    type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 5,
    phase: "text", specialty: "webrecon", content: "x",
  });
  // ...render SessionStatusBar...
  expect(screen.getByText(/webrecon/i)).toBeTruthy();
  expect(screen.getByText(/sub-turn 5/i)).toBeTruthy();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && npm test -- status-bar`
Expected: FAIL — no `webrecon` / `sub-turn 5` chip rendered.

- [ ] **Step 3: Add the chip to SessionStatusBar**

In `SessionStatusBar.tsx`, add imports:

```tsx
import { Boxes } from "lucide-react";
import { selectActiveDispatch } from "@/state/session-store";
```

Inside the component, after the existing `const llmStatus = useStore(...)` line, add:

```tsx
  const activeDispatch = useStore(store, selectActiveDispatch);
  const activeSubTurn = activeDispatch
    ? Math.max(0, ...[...activeDispatch.subTurns.keys()], 0)
    : 0;
```

Render the chip — place it right after the status pill `</span>` (after the closing of the first `<span className={cn("inline-flex ...")}>{status}</span>` block), before the `<span className="h-4 w-px bg-neutral-800" />` divider:

```tsx
          {status === "running" && activeDispatch && (
            <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-fuchsia-500/25 bg-fuchsia-500/10 px-2 py-1 text-fuchsia-200">
              <Boxes className="h-3.5 w-3.5" />
              {activeDispatch.specialty}
              {activeSubTurn > 0 && <span className="text-fuchsia-300/70">· sub-turn {activeSubTurn}</span>}
            </span>
          )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd desktop && npm test -- status-bar`
Expected: PASS.

- [ ] **Step 5: Full frontend + backend test sweep**

Run: `cd desktop && npm test` then `cd .. && python -m pytest tests/test_dispatch_watchdog.py tests/test_session_log_dispatch.py tests/test_dispatch.py -q`
Expected: PASS across the board.

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/layout/SessionStatusBar.tsx desktop/renderer/src/layout/status-bar.test.tsx
git commit -m "feat(ui): active-dispatch chip in session status bar"
```

---

## Notes for the implementer

- **`asyncio_mode = "auto"`** is set, so `async def test_*` functions run without an explicit `@pytest.mark.asyncio` decorator (Task 1's async tests rely on this).
- **Confirm the `dispatch_specialist` return shape** before finalizing Task 2's `body` extraction — read the `summary_lines`/return near the end of `dispatch.py`. The tool result may be a dict envelope (`{"content": [{"text": ...}]}`) or a string; assert accordingly.
- **The replay seeder needs no change** — `seedFromSessionLog` (session-store.ts:417-460) already parses `start`/`end` and finalizes orphaned running dispatches. Task 3 only makes the backend write those records.
- **Idle threshold defaults:** backend 300s (`REVERSER_DISPATCH_IDLE_TIMEOUT`), UI staleness 90s (`IDLE_STALE_MS`). Tests override the backend value to keep runtime short.
```
