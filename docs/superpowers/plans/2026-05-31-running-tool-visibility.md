# Running-Tool Visibility + Tool-Aware Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a long in-flight tool (e.g. nmap) read as "running" in the UI and stop the stall watchdog from falsely aborting it, while still aborting a genuinely hung generator.

**Architecture:** Track pending tool calls via a counter updated in the dispatch `_emit` funnel. The backend watchdog uses a generous tool-budget window while a tool is outstanding and the short idle window otherwise. The frontend derives the pending tool from `toolCalls.length > toolResults.length` and shows a spinner on it, suppressing the false "idle/stalled" state.

**Tech Stack:** Python 3 / asyncio / pytest (`asyncio_mode = "auto"`); React + Zustand / TypeScript / vitest.

Spec: `docs/superpowers/specs/2026-05-31-running-tool-visibility-design.md`
Branch: `feat/dispatch-stall-watchdog` (already checked out). Backend pytest via `.devenv/state/venv/bin/python -m pytest …`; frontend via `cd desktop && npm test -- <pattern>`.

---

### Task 1: Tool-budget timeout + tool-aware watchdog primitive (backend, pure)

**Files:**
- Modify: `src/reverser/tools/dispatch.py` — add `_dispatch_tool_timeout()` after `_dispatch_idle_timeout()` (~line 411); extend `_aiter_with_idle_timeout` (~line 414); ensure `Callable` import
- Test: `tests/test_dispatch_watchdog.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch_watchdog.py`:

```python
from reverser.tools.dispatch import _dispatch_tool_timeout


def test_tool_timeout_reads_env(monkeypatch):
    monkeypatch.delenv("REVERSER_DISPATCH_TOOL_TIMEOUT", raising=False)
    assert _dispatch_tool_timeout() == 1800.0
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "42.5")
    assert _dispatch_tool_timeout() == 42.5
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "garbage")
    assert _dispatch_tool_timeout() == 1800.0


async def test_pending_tool_uses_tool_window_not_idle():
    """When a tool is pending, a gap shorter than tool_seconds but longer than
    idle_seconds must NOT raise — the long tool budget applies."""
    async def gen():
        yield 0
        await asyncio.sleep(0.4)   # > idle (0.2), < tool (2.0)
        yield 1

    result = await _collect(_aiter_with_idle_timeout(
        gen(), 0.2, tool_seconds=2.0, is_tool_pending=lambda: True,
    ))
    assert result == [0, 1]


async def test_no_pending_tool_uses_idle_window():
    """When no tool is pending, the short idle window applies and a long gap raises."""
    async def gen():
        yield 0
        await asyncio.sleep(2)     # > idle (0.2)
        yield 1

    with pytest.raises(_DispatchStalled):
        await _collect(_aiter_with_idle_timeout(
            gen(), 0.2, tool_seconds=5.0, is_tool_pending=lambda: False,
        ))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.devenv/state/venv/bin/python -m pytest tests/test_dispatch_watchdog.py -v`
Expected: FAIL — `ImportError: cannot import name '_dispatch_tool_timeout'` (and the new watchdog tests error on unexpected `tool_seconds`/`is_tool_pending` kwargs).

- [ ] **Step 3: Ensure `Callable` is importable**

At the top of `src/reverser/tools/dispatch.py`, the import `from collections.abc import AsyncIterator` exists (added by a prior task). Change it to:

```python
from collections.abc import AsyncIterator, Callable
```

- [ ] **Step 4: Add `_dispatch_tool_timeout()`**

Immediately after `_dispatch_idle_timeout()` (after its `return 300.0` block, ~line 411):

```python
def _dispatch_tool_timeout() -> float:
    """Seconds a single in-flight tool call may run before the watchdog aborts the
    dispatch. Generous (default 1800s / 30 min) so real scans finish, but bounded so a
    hung MCP server or background task can't wedge the session forever. Override with
    REVERSER_DISPATCH_TOOL_TIMEOUT; a malformed value falls back to the default."""
    raw = _os.environ.get("REVERSER_DISPATCH_TOOL_TIMEOUT")
    if raw is None:
        return 1800.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1800.0
```

- [ ] **Step 5: Extend `_aiter_with_idle_timeout`**

Replace the current definition (signature + body, ~lines 414-435) with:

```python
async def _aiter_with_idle_timeout(
    agen: AsyncIterator,
    idle_seconds: float,
    *,
    tool_seconds: float | None = None,
    is_tool_pending: Callable[[], bool] | None = None,
) -> AsyncIterator:
    """Yield from ``agen``, raising ``_DispatchStalled`` if any single step idles
    longer than the active window. While a tool call is outstanding
    (``is_tool_pending()`` true and ``tool_seconds`` given), the generous
    ``tool_seconds`` window applies so a legitimately long tool is not aborted;
    otherwise the short ``idle_seconds`` window applies. Best-effort closes the
    underlying iterator on stall so the specialist generator is not leaked."""
    it = agen.__aiter__()
    while True:
        if (
            is_tool_pending is not None
            and tool_seconds is not None
            and is_tool_pending()
        ):
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

(The only behavioral change for existing 2-arg callers: none — `tool_seconds`/`is_tool_pending` default to None, so the `else: timeout = idle_seconds` branch is taken exactly as before. The `raise _DispatchStalled(timeout)` now passes the window that fired, which for the 2-arg case equals `idle_seconds` — identical to before.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.devenv/state/venv/bin/python -m pytest tests/test_dispatch_watchdog.py -v`
Expected: PASS — all prior watchdog tests (unchanged behavior) plus the 3 new ones. The pending-tool test completes in ~0.4s; the no-pending test in ~0.2s.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch_watchdog.py
git commit -m "feat(dispatch): tool-budget timeout + tool-aware idle watchdog primitive"
```

---

### Task 2: Pending-tool counter + wire tool-awareness into `_run_specialist`

**Files:**
- Modify: `src/reverser/tools/dispatch.py` — add `_pending_tools` counter (~line 585, next to `_sub_turn`); update `_emit` (~line 587); wire both watchdog call sites in `_run_specialist`
- Test: `tests/test_dispatch_watchdog.py` (append integration tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch_watchdog.py`:

```python
def test_dispatch_does_not_abort_while_tool_pending(monkeypatch, tmp_path):
    """A specialist that emits a tool_call (no result yet) then pauses longer than the
    idle window but within the tool window must NOT be aborted."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "0.3")
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "5")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def tool_then_finish(prompt, options):
        from claude_agent_sdk import (
            AssistantMessage, TextBlock, ToolUseBlock, UserMessage,
            ToolResultBlock, ResultMessage,
        )
        # sub-turn 1: a tool call, then a gap > idle (0.3s) but < tool (5s)
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="nmap", input={"target": "x"})], model="claude")
        await asyncio.sleep(0.6)
        # tool result arrives -> pending clears
        yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="open: 80", is_error=False)])
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")], model="claude")
        yield ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0,
                            is_error=False, num_turns=2, session_id="t",
                            total_cost_usd=0.0, result="x")

    with patch("reverser.tools.dispatch.query", tool_then_finish):
        result = _call_tool(dispatch_specialist, {
            "specialty": "webrecon", "sub_goal": "enumerate",
            "target": "10.10.10.5", "hypothesis_id": 1,
        })
    body = result["content"][0]["text"] if isinstance(result, dict) and "content" in result else str(result)
    assert "timeout" not in body.lower()           # NOT aborted — tool was pending
    assert sess._snapshot.in_flight is None


def test_dispatch_aborts_when_idle_with_no_tool_pending(monkeypatch, tmp_path):
    """A specialist that emits final text (no tool pending) then stalls IS aborted at
    the short idle window."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    monkeypatch.setenv("REVERSER_DISPATCH_IDLE_TIMEOUT", "0.3")
    monkeypatch.setenv("REVERSER_DISPATCH_TOOL_TIMEOUT", "30")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def text_then_hang(prompt, options):
        from claude_agent_sdk import AssistantMessage, TextBlock
        yield AssistantMessage(content=[TextBlock(text="done enumerating")], model="claude")
        await asyncio.sleep(10)    # no tool pending -> idle window (0.3s) applies

    with patch("reverser.tools.dispatch.query", text_then_hang):
        result = _call_tool(dispatch_specialist, {
            "specialty": "webrecon", "sub_goal": "enumerate",
            "target": "10.10.10.5", "hypothesis_id": 1,
        })
    body = result["content"][0]["text"] if isinstance(result, dict) and "content" in result else str(result)
    assert "timeout" in body.lower()               # aborted at idle window
    assert sess._snapshot.in_flight is None
```

> If the `claude_agent_sdk` block class constructors differ (e.g. `ToolUseBlock`/`ToolResultBlock` arg names), read how the existing `tests/test_dispatch_in_flight.py` / dispatch.py consume them and adjust the test's message construction so the dispatch's claude-path loop emits a `tool_call` then a `tool_result`. The assertions (no-abort vs abort) must hold.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.devenv/state/venv/bin/python -m pytest tests/test_dispatch_watchdog.py::test_dispatch_does_not_abort_while_tool_pending -v`
Expected: FAIL — without tool-awareness wired in, the 0.6s gap after the tool_call exceeds the 0.3s idle window and the dispatch aborts with `timeout` (assertion `"timeout" not in body` fails).

- [ ] **Step 3: Add the `_pending_tools` counter**

In `_run_specialist`'s enclosing scope, next to `_sub_turn = [0]` (~line 585), add:

```python
    _pending_tools = [0]
```

- [ ] **Step 4: Update `_emit` to track pending tools**

In `_emit` (~line 587), add the counter update at the VERY TOP of the function, BEFORE the `if sess is None: return` guard (so the counter stays accurate even without a session):

```python
    def _emit(kind: str, content: str) -> None:
        if kind == "tool_call":
            _pending_tools[0] += 1
        elif kind in ("tool_result", "tool_error"):
            _pending_tools[0] = max(0, _pending_tools[0] - 1)
        if sess is None:
            return
        # ...rest of _emit unchanged...
```

- [ ] **Step 5: Capture timeouts + pending predicate and wire both call sites**

Near the top of `_run_specialist`, after the existing `_idle = _dispatch_idle_timeout()` line, add:

```python
        _tool = _dispatch_tool_timeout()
        _pending = lambda: _pending_tools[0] > 0
```

Update the session-backend call site (currently `async for event in _aiter_with_idle_timeout(backend.run(...), _idle):`) to:

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
                    tool_seconds=_tool,
                    is_tool_pending=_pending,
                ):
```

Update the claude-SDK call site (currently `async for message in _aiter_with_idle_timeout(query(prompt=prompt, options=options), _idle):`) to:

```python
            async for message in _aiter_with_idle_timeout(
                query(prompt=prompt, options=options),
                _idle,
                tool_seconds=_tool,
                is_tool_pending=_pending,
            ):
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.devenv/state/venv/bin/python -m pytest tests/test_dispatch_watchdog.py -v`
Expected: PASS — both new integration tests plus all earlier watchdog tests.

- [ ] **Step 7: Regression sweep**

Run: `.devenv/state/venv/bin/python -m pytest tests/test_dispatch.py tests/test_dispatch_in_flight.py tests/test_dispatch_event_callback.py tests/test_session_log_dispatch.py -q`
Expected: PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
git add src/reverser/tools/dispatch.py tests/test_dispatch_watchdog.py
git commit -m "feat(dispatch): pause idle watchdog while a tool call is in flight"
```

---

### Task 3: `pendingToolCall` store helper (frontend)

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts` — export `pendingToolCall` (near `selectActiveDispatch`, bottom of file)
- Test: `desktop/renderer/src/state/session-store.test.ts` (append)

- [ ] **Step 1: Write the failing test**

Append to `desktop/renderer/src/state/session-store.test.ts`:

```ts
import { pendingToolCall } from "./session-store";
import type { SubTurn } from "./session-store";

describe("pendingToolCall", () => {
  function st(calls: number, results: number): SubTurn {
    return {
      thinkingDeltas: [], speechDeltas: [],
      toolCalls: Array.from({ length: calls }, (_, i) => ({ name: "", content: `nmap ${i}` })),
      toolResults: Array.from({ length: results }, () => ({ ok: true, content: "r" })),
    };
  }

  it("returns the unmatched tool call when calls outnumber results", () => {
    expect(pendingToolCall(st(2, 1))).toEqual({ name: "", content: "nmap 1" });
  });

  it("returns null when calls and results balance", () => {
    expect(pendingToolCall(st(2, 2))).toBeNull();
  });

  it("returns null when there are no tool calls", () => {
    expect(pendingToolCall(st(0, 0))).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test -- session-store`
Expected: FAIL — `pendingToolCall` is not exported.

- [ ] **Step 3: Add the helper**

In `desktop/renderer/src/state/session-store.ts`, near `selectActiveDispatch` (bottom of file), add:

```ts
/** The pending (unmatched) tool call in a sub-turn — a tool still executing — or null. */
export function pendingToolCall(st: SubTurn): { name: string; content: string } | null {
  return st.toolCalls.length > st.toolResults.length
    ? st.toolCalls[st.toolResults.length]
    : null;
}
```

(`SubTurn` is already defined/exported in this file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test -- session-store`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(ui): pendingToolCall store helper"
```

---

### Task 4: SubTurnBubble running-tool spinner

**Files:**
- Modify: `desktop/renderer/src/panes/SubTurnBubble.tsx`
- Test: `desktop/renderer/src/panes/sub-turn-bubble.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

Create `desktop/renderer/src/panes/sub-turn-bubble.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SubTurnBubble } from "./SubTurnBubble";
import type { SubTurn } from "@/state/session-store";

function st(): SubTurn {
  return {
    thinkingDeltas: [], speechDeltas: [],
    toolCalls: [{ name: "", content: "nmap -sV 10.10.10.5" }],
    toolResults: [],
  };
}

describe("SubTurnBubble running tool", () => {
  it("marks the running tool with a 'running' tag when runningToolIndex matches", () => {
    render(<SubTurnBubble subTurn={st()} num={1} specialty="webrecon" runningToolIndex={0} />);
    expect(screen.getByText(/running/i)).toBeTruthy();
  });

  it("shows no 'running' tag when runningToolIndex is -1 (default)", () => {
    render(<SubTurnBubble subTurn={st()} num={1} specialty="webrecon" />);
    expect(screen.queryByText(/running/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test -- sub-turn-bubble`
Expected: FAIL — no "running" tag rendered; `runningToolIndex` prop unknown.

- [ ] **Step 3: Add the prop and spinner**

In `desktop/renderer/src/panes/SubTurnBubble.tsx`:

Add `Loader2` to the lucide import (line 2):
```tsx
import { Brain, Terminal, CheckCircle2, XCircle, MessageSquare, Loader2 } from "lucide-react";
```

Add `runningToolIndex` to the props (default -1):
```tsx
export function SubTurnBubble({
  subTurn,
  num,
  specialty,
  runningToolIndex = -1,
}: {
  subTurn: SubTurn;
  num: number;
  specialty: string;
  runningToolIndex?: number;
}) {
```

Replace the `toolCalls.map(...)` block (lines 40-45) with a version that marks the running tool:
```tsx
        {subTurn.toolCalls.map((tc, i) => {
          const running = i === runningToolIndex;
          return (
            <div
              key={`tc-${i}`}
              className={cn(
                "flex gap-2 font-mono",
                running ? "text-amber-200" : "text-cyan-300/90",
              )}
            >
              {running
                ? <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" />
                : <Terminal className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
              <span className="min-w-0 whitespace-pre-wrap break-words">
                {running && <span className="mr-1 font-medium not-italic text-amber-300">running</span>}
                {tc.content}
              </span>
            </div>
          );
        })}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test -- sub-turn-bubble`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/panes/SubTurnBubble.tsx desktop/renderer/src/panes/sub-turn-bubble.test.tsx
git commit -m "feat(ui): SubTurnBubble spinner on the running tool call"
```

---

### Task 5: DispatchPanel pending-tool wiring (header, staleness suppression, icon)

**Files:**
- Modify: `desktop/renderer/src/panes/DispatchPanel.tsx`
- Test: `desktop/renderer/src/panes/dispatch-panel.test.tsx` (append)

- [ ] **Step 1: Write the failing tests**

Append to `desktop/renderer/src/panes/dispatch-panel.test.tsx` (the file already imports `render, screen, vi, Dispatch` and sets up fake timers):

```tsx
import type { SubTurn } from "@/state/session-store";

function dispatchWithPendingTool(lastActivityAt: number): Dispatch {
  const sub: SubTurn = {
    thinkingDeltas: [], speechDeltas: [],
    toolCalls: [{ name: "", content: "nmap -sV 10.10.10.5" }],
    toolResults: [],
  };
  return {
    id: "dp", specialty: "webrecon", subGoal: "enumerate",
    status: "running", subTurns: new Map([[1, sub]]), lastActivityAt,
  };
}

describe("DispatchPanel running tool", () => {
  it("shows 'running nmap' and no idle marker even past the stale threshold", () => {
    vi.setSystemTime(new Date("2026-05-31T18:00:00Z"));
    // 120s since last activity would normally be 'stale', but a tool is pending
    render(<DispatchPanel dispatch={dispatchWithPendingTool(Date.now() - 120_000)} />);
    expect(screen.getByText(/running nmap/i)).toBeTruthy();
    expect(screen.queryByText(/idle/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test -- dispatch-panel`
Expected: FAIL — no "running nmap" text, and the idle marker is shown (pending not yet suppressing staleness).

- [ ] **Step 3: Wire pending-tool logic into DispatchPanel**

In `desktop/renderer/src/panes/DispatchPanel.tsx`:

Add to the store-helper import (currently `import type { Dispatch } from "@/state/session-store";`):
```tsx
import { pendingToolCall, type Dispatch } from "@/state/session-store";
```

Add a tool-name helper near the top (after the existing `trimDetail` function):
```tsx
function toolName(content: string): string {
  return content.trim().split(/\s+/)[0] || "tool";
}
```

Inside `DispatchPanel`, the component already computes `subTurns` (sorted ascending) and `activity`, then `now`/`idleMs`/`isStale`/`idleLabel`. Just AFTER the existing `const activity = latestActivity(dispatch);` line, add the active sub-turn + pending detection:
```tsx
  const activeSubTurn = subTurns.length ? subTurns[subTurns.length - 1] : null; // [num, SubTurn]
  const pending = dispatch.status === "running" && activeSubTurn
    ? pendingToolCall(activeSubTurn[1])
    : null;
```

Change the `isStale` computation to suppress staleness while a tool is pending. Replace the existing line:
```tsx
  const isStale = idleMs > IDLE_STALE_MS;
```
with:
```tsx
  const isStale = !pending && idleMs > IDLE_STALE_MS;
```

Update the `StatusIcon` ternary so a pending tool shows a spinner (active), taking precedence over stale:
```tsx
  const StatusIcon = dispatch.status === "completed" ? CheckCircle2
    : dispatch.status === "error" ? XCircle
    : dispatch.status === "timeout" ? Clock3
    : pending ? Loader2
    : isStale ? AlertTriangle
    : activity?.label === "queued on local backend" ? Clock3
      : Loader2;
```

Ensure the spin animation runs when pending. The spin condition currently is roughly `dispatch.status === "running" && !isStale && activity?.label !== "queued on local backend" && "animate-spin"`. Since `pending` forces `isStale` false, this already spins for a pending tool — leave it.

Add the "running <tool>" header label. Find the activity/idle label spans (~line 71-72):
```tsx
          {activity && <span className="ml-2 text-neutral-500">· {activity.label}</span>}
          {idleLabel && <span className="ml-2 text-amber-400">· {idleLabel}</span>}
```
Replace them with a pending-aware version (pending takes precedence; idle only shows when not pending — it already can't be stale when pending, but guard the label too):
```tsx
          {pending
            ? <span className="ml-2 text-amber-300">· running {toolName(pending.content)}</span>
            : activity && <span className="ml-2 text-neutral-500">· {activity.label}</span>}
          {!pending && idleLabel && <span className="ml-2 text-amber-400">· {idleLabel}</span>}
```

Pass `runningToolIndex` to the active sub-turn's `SubTurnBubble`. The render maps `subTurns.map(([n, st]) => <SubTurnBubble ... />)`. Update it to compute the running index for the active sub-turn only:
```tsx
          {subTurns.map(([n, st]) => (
            <SubTurnBubble
              key={n}
              subTurn={st}
              num={n}
              specialty={dispatch.specialty}
              runningToolIndex={pending && activeSubTurn && n === activeSubTurn[0] ? st.toolResults.length : -1}
            />
          ))}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test -- dispatch-panel`
Expected: PASS — the new running-tool test plus the existing idle/timeout tests.

- [ ] **Step 5: Full sweep (frontend + backend)**

Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/desktop && npm test`
Expected: all pass.
Run: `cd /Users/jrizzo/Projects/gitea/johnrizzo1/reverser && .devenv/state/venv/bin/python -m pytest tests/test_dispatch_watchdog.py tests/test_dispatch.py tests/test_dispatch_in_flight.py tests/test_session_log_dispatch.py tests/test_dispatch_event_callback.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/panes/DispatchPanel.tsx desktop/renderer/src/panes/dispatch-panel.test.tsx
git commit -m "feat(ui): DispatchPanel shows running tool + suppresses false stall while a tool runs"
```

---

## Notes for the implementer

- `asyncio_mode = "auto"` — `async def test_*` need no decorator.
- The watchdog primitive change is backward-compatible: existing 2-arg calls behave identically (idle window only). Confirm the prior watchdog tests still pass in Task 1.
- The `_pending_tools` counter update MUST sit before the `if sess is None: return` guard in `_emit`.
- Frontend: `pending` is gated on `dispatch.status === "running"`, so completed/replay dispatches never show the running spinner or "running <tool>" label.
- Tool name comes from the emitted `tool_call` content (`"<toolName> <summarized input>"`), so `content.split(/\s+/)[0]` is the tool name.
```
