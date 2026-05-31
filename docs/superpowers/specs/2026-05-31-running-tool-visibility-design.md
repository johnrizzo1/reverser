# Running-tool visibility + tool-aware stall watchdog

**Date:** 2026-05-31
**Status:** Design — pending review
**Builds on:** `2026-05-31-dispatch-stall-watchdog-and-progress-design.md` (idle watchdog + progress UI, already implemented on `feat/dispatch-stall-watchdog`)

## Problem

A dispatched specialist that fires a long tool (e.g. a multi-minute `nmap` scan) emits
**no events between the `tool_call` and its `tool_result`**. Two consequences:

1. **UI looks dead.** The DispatchPanel shows the last `tool_call` as static text with no
   sign the tool is still executing. The analyst observed: "after a long period the nmap
   scan came back and the agent continued — it just appeared like nothing was happening."

2. **The new stall machinery misfires on legitimate long tools.** The idle watchdog
   (backend, 300 s) and the UI staleness indicator (90 s) both key off "time since last
   event." A 6-minute scan produces a 6-minute event gap, so:
   - the UI would show "idle 6m" + a stalled `AlertTriangle` while the tool is healthy;
   - the backend watchdog would **abort a legitimately-running scan** at 5 minutes.

The distinction we need: **"actively running a tool" vs "actually hung."** When a
`tool_call` has been emitted with no matching `tool_result` yet, the specialist is
executing a tool — not stalled.

## Goals

- Show which sub-turn tool is currently running, with a spinner, so long tools read as
  "working" not "dead."
- Make the stall watchdog tool-aware so it never aborts a specialist that is waiting on
  an in-flight tool, while still aborting a genuinely hung generator.
- Suppress the false "idle/stalled" UI state while a tool is in flight.

Non-goals: per-tool progress bars or streaming tool output; bounding individual tool
runtime (shell tools already self-timeout; this spec only handles the dispatch-level
view).

## Key signal: pending tool calls

Within a sub-turn, tool calls and their results balance out: a `tool_call` event is
emitted (under sub-turn N), the tool runs, then a `tool_result`/`tool_error` event
arrives (still under sub-turn N — `UserMessage`/result events don't advance the sub-turn
counter). So:

> **A tool is in flight ⟺ `toolCalls.length > toolResults.length` in the active sub-turn.**
> The pending call is `toolCalls[toolResults.length]`.

This holds for both backend paths (claude SDK `query()` and session `backend.run()`),
which both route every tool event through the dispatch's `_emit(...)` helper.

## Design

### 1. Backend: tool-aware idle watchdog — `src/reverser/tools/dispatch.py`

**Track pending tools.** Add a shared counter alongside the existing `_sub_turn = [0]`:

```python
    _pending_tools = [0]
```

Update it inside `_emit(kind, content)` (the single funnel for all sub-agent events):

```python
    def _emit(kind: str, content: str) -> None:
        if kind == "tool_call":
            _pending_tools[0] += 1
        elif kind in ("tool_result", "tool_error"):
            _pending_tools[0] = max(0, _pending_tools[0] - 1)
        # ...existing log + bus emit unchanged...
```

**Add a separate, generous tool timeout** (the backstop for hung MCP/background tools,
which shell tools' own timeouts don't cover):

```python
def _dispatch_tool_timeout() -> float:
    """Seconds a single in-flight tool call may run before the watchdog aborts the
    dispatch. Generous (default 1800s/30min) so real scans finish; bounded so a hung
    MCP server / background task can't wedge the session forever. Override with
    REVERSER_DISPATCH_TOOL_TIMEOUT; malformed value falls back to the default."""
    raw = _os.environ.get("REVERSER_DISPATCH_TOOL_TIMEOUT")
    if raw is None:
        return 1800.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1800.0
```

**Extend the watchdog primitive** to switch windows based on pending state, keeping the
existing 2-arg call sites working (new params are keyword-only with defaults):

```python
async def _aiter_with_idle_timeout(
    agen: AsyncIterator,
    idle_seconds: float,
    *,
    tool_seconds: float | None = None,
    is_tool_pending: "Callable[[], bool] | None" = None,
) -> AsyncIterator:
    it = agen.__aiter__()
    while True:
        if is_tool_pending is not None and tool_seconds is not None and is_tool_pending():
            timeout = tool_seconds
        else:
            timeout = idle_seconds
        try:
            item = await asyncio.wait_for(it.__anext__(), timeout)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            try:
                await asyncio.wait_for(it.aclose(), 30)
            except Exception:
                pass
            raise _DispatchStalled(timeout)
        yield item
```

The wrapper checks `is_tool_pending()` *before* each `__anext__`. Because `_emit`
updates the counter while the consumer processes the just-yielded event (before
requesting the next), the window in effect for "await the tool result" is correctly
`tool_seconds` whenever a `tool_call` was the most recent event. `_DispatchStalled` now
carries the timeout that actually fired (so the message reads "1800s" for a tool stall,
"300s" for an idle stall).

**Wire it at both call sites** in `_run_specialist`:

```python
        _idle = _dispatch_idle_timeout()
        _tool = _dispatch_tool_timeout()
        _pending = lambda: _pending_tools[0] > 0
        ...
        async for event in _aiter_with_idle_timeout(
            backend.run(...), _idle, tool_seconds=_tool, is_tool_pending=_pending,
        ):
        ...
        async for message in _aiter_with_idle_timeout(
            query(prompt=prompt, options=options), _idle,
            tool_seconds=_tool, is_tool_pending=_pending,
        ):
```

The `except _DispatchStalled` handler is unchanged (`status="timeout"`, partial report
preserved) — it already uses `e.idle_seconds`, which now reflects whichever window fired.

**Why this is safe for the original bug:** the devhub hang occurred *after* the
specialist emitted its final text (`_pending_tools == 0`), so the short 300 s idle window
still applies and still aborts it. Tool-awareness only relaxes the window while a tool is
genuinely outstanding.

### 2. Frontend: in-flight tool indicator

**Pure helper** in `desktop/renderer/src/state/session-store.ts`:

```ts
/** The pending (unmatched) tool call in a sub-turn — a tool still executing — or null. */
export function pendingToolCall(st: SubTurn): { name: string; content: string } | null {
  return st.toolCalls.length > st.toolResults.length
    ? st.toolCalls[st.toolResults.length]
    : null;
}
```

A tiny display helper (co-located in DispatchPanel) extracts the tool name from the
emitted `content` (`"nmap {...}"` → `"nmap"`): `content.trim().split(/\s+/)[0]`.

**DispatchPanel** (`desktop/renderer/src/panes/DispatchPanel.tsx`):
- Compute the active sub-turn (highest key) and `const pending = dispatch.status === "running" ? pendingToolCall(activeSubTurn) : null;`.
- **Suppress false staleness while a tool runs:** `const isStale = !pending && dispatch.status === "running" && idleMs > IDLE_STALE_MS;`.
- **StatusIcon precedence:** completed→`CheckCircle2`, error→`XCircle`, timeout→`Clock3`,
  `pending`→`Loader2` (spinning — actively working), `isStale`→`AlertTriangle`,
  queued→`Clock3`, else `Loader2`.
- **Header label:** when `pending`, show `· running {toolName(pending.content)}` (cyan)
  taking precedence over the activity/idle label; otherwise the existing label + idle
  label.
- Pass `runningToolIndex` to the active sub-turn's `SubTurnBubble` (= `toolResults.length`
  when `pending`, else `-1`); pass `-1` to all other sub-turns.

**SubTurnBubble** (`desktop/renderer/src/panes/SubTurnBubble.tsx`): accept an optional
`runningToolIndex = -1` prop. When rendering `toolCalls[i]` and `i === runningToolIndex`,
swap the static `Terminal` icon for a spinning `Loader2` and prefix the line with a
muted "running" tag, so the executing tool is visually distinct from completed calls.

The SessionStatusBar chip (from the prior spec) already shows `specialty · sub-turn N`;
no change there — the per-tool detail lives in the panel.

## Data flow (after)

```
specialist emits tool_call (nmap)  → _emit: _pending_tools=1
  → watchdog now bounds the await by _tool (1800s), not _idle (300s)  [no false abort]
  → UI: pendingToolCall(activeSubTurn) ≠ null
       → DispatchPanel: spinner + "running nmap", isStale forced false
       → SubTurnBubble: spinning Loader2 on the nmap line
… tool finishes, tool_result arrives → _emit: _pending_tools=0
  → watchdog back to _idle; UI spinner clears, normal activity resumes
generator hangs with no tool pending (original bug) → _idle (300s) fires → timeout abort
```

## Error handling

- Counter floored at 0 (`max(0, …)`) so an unmatched `tool_result` can't drive it
  negative.
- `is_tool_pending`/`tool_seconds` both optional — existing 2-arg watchdog callers and
  their tests are unaffected.
- Frontend helper returns null for any sub-turn with balanced or zero calls; the panel
  degrades to existing behavior when there's no active sub-turn.
- A replay/historical dispatch (`status !== "running"`) never shows the running-tool
  spinner (`pending` gated on running).

## Testing

- **Backend unit:** `_dispatch_tool_timeout` env parsing (default/valid/garbage).
  `_aiter_with_idle_timeout` with `is_tool_pending` returning True uses `tool_seconds`
  (a generator idle 0.4 s with `idle=0.2, tool=2.0, pending=True` does NOT raise; with
  `pending=False` it DOES raise). Backward-compat: 2-arg call still raises on idle.
- **Backend integration:** a stub `query` that emits a `tool_call` (no result) then
  sleeps — with `REVERSER_DISPATCH_IDLE_TIMEOUT=0.3` and
  `REVERSER_DISPATCH_TOOL_TIMEOUT=5`, the dispatch does NOT abort at 0.3 s (tool pending);
  a stub that emits a final text then sleeps DOES abort at 0.3 s (no tool pending).
- **Frontend (vitest):** `pendingToolCall` returns the unmatched call / null. DispatchPanel
  shows "running nmap" + no idle marker when a tool is pending even past 90 s idle;
  SubTurnBubble renders the spinner on `runningToolIndex`. A completed dispatch shows no
  running spinner.

## Files touched

- `src/reverser/tools/dispatch.py` — `_pending_tools` counter in `_emit`,
  `_dispatch_tool_timeout()`, extended `_aiter_with_idle_timeout`, both call sites.
- `desktop/renderer/src/state/session-store.ts` — `pendingToolCall` helper.
- `desktop/renderer/src/panes/DispatchPanel.tsx` — pending detection, staleness
  suppression, header label, icon precedence, `runningToolIndex` prop.
- `desktop/renderer/src/panes/SubTurnBubble.tsx` — `runningToolIndex` spinner.
- Tests alongside each.
