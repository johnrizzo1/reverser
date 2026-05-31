# Dispatch stall watchdog + sub-agent progress visibility

**Date:** 2026-05-31
**Status:** Design — pending review

## Problem

When the manager calls `dispatch_specialist`, the session badge shows **running** and
never transitions, even after the specialist has effectively finished. Two distinct
failures combine:

1. **A stalled specialist wedges the whole session forever.** The dispatch body
   (`src/reverser/tools/dispatch.py`) iterates the specialist's event generator
   (`query()` for the claude backend, `backend.run()` for session backends) with **no
   wall-clock guard**. If that generator stops yielding — e.g. a hung background task or
   a dead MCP stdio server — the `async for` blocks indefinitely. Because
   `_run_specialist` never returns:
   - the `finally: _emit_end(...)` (dispatch.py:775) never fires → the DispatchPanel
     spinner spins forever and no `"end"` frame reaches the UI;
   - `in_flight` is never cleared (dispatch.py:777);
   - the `dispatch_specialist` tool call never returns → the manager's `send()` loop
     (`session_adapter.py:185`) stays awaiting → the `awaiting_input` status frame
     (line 198) is never published → the badge stays **running**.

   **Confirmed in a live session:** `targets/10.129.6.125/sessions/2026-05-31T13-39-48-994958.json`
   has `state=active` with `in_flight={dispatch, webrecon, started_at 17:46:18}`
   un-cleared; the dispatch's last event was at 17:53:27 and it stayed silent for 25+
   minutes while the GUI process was alive and blocked. There is no `wait_for`/`timeout`
   anywhere in `dispatch.py`.

2. **A healthy-but-slow dispatch is visually indistinguishable from a dead one.** The
   top-level status bar only knows `running` vs `awaiting_input`. The DispatchPanel
   spinner animates identically whether sub-agent events are flowing or stopped 25
   minutes ago. There is no "a sub-agent is active" indicator and no staleness signal.

Secondary: `_emit_start`/`_emit_end` write only to the WebSocket bus, never to the JSONL
session log (only `_emit` does). The replay path (`seedFromSessionLog`) already knows how
to consume dispatch `start`/`end` records — it's just never given any.

## Goals

- A stalled specialist always terminates the dispatch within a bounded idle window,
  unblocking the session and returning a partial report to the manager.
- The analyst can tell at a glance whether a dispatch is actively progressing or stuck.
- Dispatch start/end render correctly on replay/resume.

Non-goals: fixing *why* a given specialist's generator hangs (hung MCP servers, runaway
background tasks) — that's upstream of this; the watchdog makes it survivable. No change
to the existing `max_turns` / budget limits.

## Design

### 1. Idle-timeout watchdog (backend) — `src/reverser/tools/dispatch.py`

Wrap both specialist event loops so each step is bounded by an **idle** timeout: time
since the *last* event, not total wall-clock. Healthy specialists emit turns/text/tool
events continuously, so an idle gap is a precise stall signal and never penalizes a
legitimately long run.

Add a small async wrapper:

```python
class _DispatchStalled(Exception):
    """Raised when a dispatched specialist emits no event within the idle window."""

async def _aiter_with_idle_timeout(agen, idle_seconds: float):
    """Yield from `agen`, raising _DispatchStalled if any single step idles too long.
    Closes the underlying generator on stall so we don't leak the subprocess."""
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

Apply it to both branches inside `_run_specialist`:

```python
idle = _dispatch_idle_timeout()  # seconds
...
async for event in _aiter_with_idle_timeout(backend.run(...), idle):
    ...
# and:
async for message in _aiter_with_idle_timeout(query(prompt=prompt, options=options), idle):
    ...
```

Catch the stall and convert it to a terminal `timeout` status instead of a generic
error, preserving any partial report already streamed:

```python
try:
    report_text = await _run_specialist(sub_goal)
    ...
except _DispatchStalled as e:
    status = "timeout"
    error_msg = f"specialist produced no output for {int(e.args[0])}s — aborted (stall watchdog)"
    if not report_text:
        report_text = f"(dispatch aborted: {error_msg})"
    outcome = "inconclusive"
    report_model = None
except Exception as e:
    ...  # unchanged
finally:
    _emit_end(status, cost_usd, turns_consumed)   # unchanged — now also reached on stall
    ...                                            # in_flight cleared — unchanged
```

The existing `finally` already does the right thing once the loop unblocks: emits the
`end` frame, clears `in_flight`, and the summary block surfaces `Status: timeout` with
the partial report so the manager can decide next steps.

**Threshold:** default **300 s (5 min)** idle, overridable via env
`REVERSER_DISPATCH_IDLE_TIMEOUT` (seconds) through a `_dispatch_idle_timeout()` helper
for testability and field-tuning. The local-backend "waiting for slot" path already
emits a `thinking` heartbeat on acquire, so queued dispatches don't trip the watchdog
while waiting; if slot-wait could exceed the window, the watchdog wraps only the
post-acquire `run()` loop (slot acquisition stays outside the wrapper).

`timeout` joins the existing non-success status vocabulary (`budget_exhausted`,
`turn_limit`, `error`); `_promote_status` and the summary block treat it like the other
non-success states (no special casing needed beyond the label).

### 2. Persist start/end to the session log — `dispatch.py`

In `_emit_start` and `_emit_end`, also call `sess._slog.log_dispatch_event(specialty,
"start"|"end", <json payload>, dispatch_id=dispatch_id, sub_turn=0)` (guarded in
try/except like the existing `_emit`). No frontend change needed: `seedFromSessionLog`
(session-store.ts:417-460) already parses `start`/`end` records and finalizes orphaned
running dispatches.

### 3. Sub-agent progress + staleness (frontend)

**a. Track last activity per dispatch.** Add `lastActivityAt?: number` to the `Dispatch`
type (session-store.ts:49). Stamp it with `Date.now()` in the `ingest` `"dispatch"` case
on every frame (start/sub-turn/end). Live WS path only; the replay seed leaves it
undefined (replays are historical).

**b. DispatchPanel staleness (`desktop/renderer/src/panes/DispatchPanel.tsx`).** A
lightweight ticking clock (a shared `useNow(15_000)` hook or a local `setInterval`) lets
the panel recompute idle time. While `status === "running"`:
- idle ≤ 90 s → current spinner, unchanged;
- idle > 90 s → append `· idle <Nm>` to the activity line and swap the spinning
  `Loader2` for a non-spinning amber `AlertTriangle` to signal "no recent activity."
This is purely cosmetic and self-corrects the moment a new frame arrives or the `end`
frame lands.

**c. Status bar active-dispatch indicator (`SessionStatusBar.tsx`).** Add a store
selector that returns the running dispatch in the current turn (specialty + max
sub-turn), if any. When `status === "running"` and such a dispatch exists, render a chip
next to the status pill: `webrecon · sub-turn 64` (and `· idle Nm` when stale, reusing
the same threshold). This makes "a sub-agent is doing the work" legible at the top level.

## Data flow (after)

```
specialist generator stalls (no event > 300s)
  → _aiter_with_idle_timeout raises _DispatchStalled, aclose()s the generator
  → _run_specialist unwinds; dispatch body sets status="timeout"
  → finally: _emit_end("timeout") + log_dispatch_event("end") + in_flight=None
  → tool returns partial report → manager send() resumes
  → session_adapter publishes status "awaiting_input"  → badge flips off "running"
Frontend, meanwhile:
  → each dispatch frame stamps lastActivityAt; panel shows "idle Nm" after 90s
  → status bar chip shows "webrecon · sub-turn N (· idle Nm)" while running
  → "end" frame flips dispatch to completed/error/timeout → spinner stops
```

## Error handling

- Watchdog wrapper swallows `aclose()` errors (best-effort cleanup; never mask the stall).
- A stall during slot-wait is avoided by wrapping only the post-acquire loop.
- `_emit_end` is already in `finally`, so every exit path (success, stall, error, budget,
  turn limit) emits exactly one `end` frame and clears `in_flight`.
- Frontend clock interval is cleared on unmount; idle display degrades gracefully when
  `lastActivityAt` is undefined (replay) → no idle chip shown.

## Testing

- **Backend unit (pytest):** feed `_aiter_with_idle_timeout` a fake async generator that
  sleeps past the idle window → asserts `_DispatchStalled` raised and the generator's
  `aclose()` was called. Feed a fast generator → all items pass through, no raise.
- **Backend integration:** a stub specialist that yields a few events then sleeps forever;
  assert the dispatch returns within ~idle+ε with `status=timeout`, a partial report, an
  `end` frame emitted, and `in_flight` cleared on the snapshot.
- **Frontend (vitest):** ingest a `start` + sub-turn frames, advance fake timers past 90 s
  with no further frames → `latestActivity`/panel reports idle and renders the stalled
  icon; then ingest `end` → status flips and idle clears. Status-bar selector returns the
  running dispatch given a seeded store.
- **Replay:** seed a log containing `start`+`end` records → dispatch renders terminal (not
  spinning), confirming #2 end-to-end.

## Files touched

- `src/reverser/tools/dispatch.py` — watchdog wrapper, `_dispatch_idle_timeout()`,
  `timeout` status handling, `start`/`end` log persistence.
- `desktop/renderer/src/state/session-store.ts` — `lastActivityAt` field + stamping,
  active-dispatch selector.
- `desktop/renderer/src/panes/DispatchPanel.tsx` — idle display + stalled icon, clock.
- `desktop/renderer/src/layout/SessionStatusBar.tsx` — active-dispatch chip.
- Tests alongside each.
