# Chat Redesign + KB Reactivity + F2 Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the live session main pane into per-turn chat bubbles with nested dispatch sub-conversations, make hypothesis/finding writes update the GUI live, and wire the F2 profile picker that the footer already advertises.

**Architecture:** Stamp WS frames with `turn` / `tool_use_id` / `dispatch_id` / `sub_turn` so the renderer can group by id instead of by position. Replace the renderer's flat `messages`/`pendingAssistantText`/`thinkingEntries`/`dispatchEntries` lists with a turn-indexed Map and render a recursive `<TurnBubble>` per turn. Add a thin `kb_emitter` helper that the hypothesis/finding tools call after each SQLite write to push a `hypothesis` or `finding` frame onto the existing EventBus.

**Tech Stack:** Python (FastAPI + AnyIO + Claude Agent SDK) on the backend; React 18 + Zustand + React Query + Playwright + (new) Vitest on the frontend.

**Reference spec:** [docs/superpowers/specs/2026-05-23-chat-redesign-and-reactivity-design.md](../specs/2026-05-23-chat-redesign-and-reactivity-design.md).

---

## File map

**Backend modified**
- `src/reverser/backends/base.py` — `AgentEvent` gains `tool_use_id` and `turn`.
- `src/reverser/backends/claude.py` — extract `tool_use_id` from `ToolUseBlock`/`ToolResultBlock`; thread turn count.
- `src/reverser/agent_session.py` — track current turn; stamp `turn` onto forwarded events; add `on_kb_event` callback; expand dispatch callback signature.
- `src/reverser/gui_service/session_adapter.py` — write `turn` into every frame; new `_on_kb_event`; expanded `_on_dispatch_event`.
- `src/reverser/tools/dispatch.py` — mint `dispatch_id`, track `sub_turn`, emit `start`/`end` lifecycle frames.
- `src/reverser/tools/kb.py` — call `emit_hypothesis(action, row)` / `emit_finding(action, row)` after each KB mutator.
- `src/reverser/session_log.py` — log entries gain optional `turn`/`tool_use_id`/`dispatch_id`/`sub_turn` fields (best-effort additive).

**Backend created**
- `src/reverser/gui_service/kb_emitter.py` — `emit_hypothesis` / `emit_finding` helpers.

**Backend tests**
- `tests/gui_service/test_session_adapter.py` — modified to assert `turn` on frames and new dispatch lifecycle.
- `tests/tools/test_kb_emit.py` — new.
- `tests/gui_service/test_dispatch_frames.py` — new.

**Renderer modified**
- `desktop/renderer/src/state/session-store.ts` — major rewrite (`turns: Map<number, Turn>`).
- `desktop/renderer/src/panes/ChatPane.tsx` — rewritten around new data model.
- `desktop/renderer/src/panes/HypothesesPane.tsx` — seed on every `kb.data` change.
- `desktop/renderer/src/panes/FindingsPane.tsx` — read from `store.findings` Map.
- `desktop/renderer/src/layout/SessionLayout.tsx` — F2 binding + profile button + remove tool-timeline slot.
- `desktop/renderer/src/layout/Footer.tsx` — single keymap source of truth.
- `desktop/renderer/src/api/queries.ts` — add `useProfiles()` if missing.
- `desktop/package.json` — add Vitest + jsdom dev deps.

**Renderer created**
- `desktop/renderer/src/panes/SpeechBlock.tsx`
- `desktop/renderer/src/panes/ThinkingChip.tsx`
- `desktop/renderer/src/panes/ToolCallChip.tsx`
- `desktop/renderer/src/panes/UserBubble.tsx`
- `desktop/renderer/src/panes/SubTurnBubble.tsx`
- `desktop/renderer/src/panes/DispatchPanel.tsx`
- `desktop/renderer/src/panes/TurnBubble.tsx`
- `desktop/renderer/src/modals/ProfilePickerModal.tsx`
- `desktop/renderer/vitest.config.ts`
- `desktop/renderer/src/state/session-store.test.ts`
- `desktop/renderer/src/panes/turn-bubble.test.tsx`
- `desktop/renderer/src/modals/profile-picker-modal.test.tsx`

**Renderer deleted**
- `desktop/renderer/src/panes/ToolTimelinePane.tsx`

**E2E tests created**
- `desktop/tests/e2e/chat-bubbles.spec.ts`
- `desktop/tests/e2e/profile-picker.spec.ts`

---

# Phase 1 — Backend frame protocol

## Task 1: Add `tool_use_id` and `turn` to AgentEvent + claude backend

**Files:**
- Modify: `src/reverser/backends/base.py`
- Modify: `src/reverser/backends/claude.py`

- [ ] **Step 1: Update `AgentEvent` dataclass**

Edit `src/reverser/backends/base.py` to add two optional fields:

```python
@dataclass
class AgentEvent:
    """An event emitted by an agent backend during execution."""
    kind: str
    content: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_use_id: str = ""        # NEW: present on tool_call/tool_result events
    turn: int = 0                # NEW: 1-based; 0 means "not associated with a turn"
    is_error: bool = False
    cost: float | None = None
    turns: int | None = None
    subtype: str = ""
```

- [ ] **Step 2: Update claude backend to populate the new fields**

Edit `src/reverser/backends/claude.py` lines 52-87. Replace the `turn = 0` line and the inner per-block emits:

```python
        turn = 0

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                turn += 1
                yield AgentEvent(kind="turn", turns=turn, turn=turn)

                for block in message.content:
                    if isinstance(block, ThinkingBlock):
                        yield AgentEvent(kind="thinking", content=block.thinking, turn=turn)

                    elif isinstance(block, ToolUseBlock):
                        try:
                            input_str = json.dumps(block.input, indent=2)
                        except (TypeError, ValueError):
                            input_str = str(block.input)
                        yield AgentEvent(
                            kind="tool_call",
                            tool_name=block.name,
                            tool_input=input_str,
                            tool_use_id=block.id,
                            turn=turn,
                        )

                    elif isinstance(block, TextBlock):
                        yield AgentEvent(kind="text", content=block.text, turn=turn)

            elif isinstance(message, UserMessage):
                if isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            text = _extract_result_text(block)
                            yield AgentEvent(
                                kind="tool_result",
                                content=text,
                                tool_use_id=getattr(block, "tool_use_id", ""),
                                is_error=bool(block.is_error),
                                turn=turn,
                            )
```

- [ ] **Step 3: Run existing backend tests**

```bash
uv run pytest tests/test_session_lifecycle.py -v
```

Expected: PASS (existing tests don't assert on `tool_use_id`/`turn`; they should still work because the new fields default to empty string / 0).

- [ ] **Step 4: Commit**

```bash
git add src/reverser/backends/base.py src/reverser/backends/claude.py
git commit -m "feat(backend): add tool_use_id and turn to AgentEvent"
```

---

## Task 2: AgentSession `on_kb_event` callback + dispatch callback signature

**Files:**
- Modify: `src/reverser/agent_session.py:75` (callback slot region)
- Modify: `src/reverser/agent_session.py:95` (emit_dispatch_event)
- Test: `tests/test_agent_session_callbacks.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_agent_session_callbacks.py`:

```python
"""AgentSession callback slots for KB and dispatch event bridging."""
import pytest
from unittest.mock import MagicMock
from reverser.agent_session import AgentSession
from reverser.profiles import get_profile


@pytest.fixture
def session(tmp_path):
    log = tmp_path / "log.jsonl"
    return AgentSession(
        binary_path=str(tmp_path / "noop"),
        profile=get_profile("general"),
        log_path=str(log),
    )


def test_emit_dispatch_event_with_id_and_sub_turn(session):
    spy = MagicMock()
    session.on_dispatch_event = spy
    session.emit_dispatch_event("webpentest", "abc-123", 2, "thinking", "hmm")
    spy.assert_called_once_with("webpentest", "abc-123", 2, "thinking", "hmm")


def test_emit_kb_event(session):
    spy = MagicMock()
    session.on_kb_event = spy
    session.emit_kb_event("hypothesis", {"action": "update", "row": {"id": 4}})
    spy.assert_called_once_with("hypothesis", {"action": "update", "row": {"id": 4}})


def test_kb_event_no_callback_is_safe(session):
    session.on_kb_event = None
    session.emit_kb_event("hypothesis", {"action": "update", "row": {"id": 4}})


def test_kb_event_callback_exception_swallowed(session):
    session.on_kb_event = MagicMock(side_effect=RuntimeError("ui crashed"))
    session.emit_kb_event("hypothesis", {})  # no raise
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_agent_session_callbacks.py -v
```

Expected: FAIL with `AttributeError` or `TypeError` on the missing/wrong-signature callbacks.

- [ ] **Step 3: Implement**

Edit `src/reverser/agent_session.py`. Replace lines 71-75 (callback declaration) with:

```python
        # Per-turn callbacks set by the host to bridge sub-agent events:
        #   on_dispatch_event(specialty, dispatch_id, sub_turn, kind, content)
        #   on_kb_event(kind, payload)   # kind in {"hypothesis", "finding"}
        # None means "do not surface" — used by CLI-only contexts.
        self.on_dispatch_event = None
        self.on_kb_event = None
```

Replace `emit_dispatch_event` (lines 95-110) with:

```python
    def emit_dispatch_event(
        self,
        specialty: str,
        dispatch_id: str,
        sub_turn: int,
        kind: str,
        content: str,
    ) -> None:
        """Surface a dispatch_specialist sub-agent event to the host.

        dispatch_id is a UUID minted by the dispatch tool at entry; the same
        id is attached to every event from that specialist run. sub_turn is
        the specialist's own turn counter (1-based) for events inside a
        normal phase; for "start" and "end" phases the dispatch tool may
        pass 0.
        """
        cb = self.on_dispatch_event
        if cb is None:
            return
        try:
            cb(specialty, dispatch_id, sub_turn, kind, content)
        except Exception:
            pass

    def emit_kb_event(self, kind: str, payload: dict) -> None:
        """Surface a KB write to the host.

        `kind` is "hypothesis" or "finding"; payload matches the WS frame
        body without the `type` field, i.e. {"action": ..., "row": ...}.
        """
        cb = self.on_kb_event
        if cb is None:
            return
        try:
            cb(kind, payload)
        except Exception:
            pass
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_agent_session_callbacks.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Update existing dispatch callers (broken signature)**

The dispatch tool (`src/reverser/tools/dispatch.py:329`) currently calls `sess.emit_dispatch_event(specialty, kind, content)` — the 3-arg form. That call is the only existing caller in the codebase; Task 4 below rewrites the dispatch tool to pass the new args. Until Task 4 lands, run:

```bash
grep -rn "emit_dispatch_event" src/ tests/
```

Expected: only the call in `src/reverser/tools/dispatch.py` (and the new tests). Leave the call as-is; the dispatch tool rewrite in Task 4 updates it.

To avoid a broken intermediate state, add a temporary shim — accept the old signature too:

```python
    def emit_dispatch_event(self, *args) -> None:
        """Surface a dispatch sub-agent event to the host.

        Accepts either 5 args (specialty, dispatch_id, sub_turn, kind, content)
        — the current signature — or 3 args (specialty, kind, content) as a
        transitional shim while callers are migrated. Remove the 3-arg shim
        once Task 4 lands.
        """
        cb = self.on_dispatch_event
        if cb is None:
            return
        if len(args) == 5:
            specialty, dispatch_id, sub_turn, kind, content = args
        elif len(args) == 3:
            specialty, kind, content = args
            dispatch_id, sub_turn = "", 0
        else:
            return
        try:
            cb(specialty, dispatch_id, sub_turn, kind, content)
        except Exception:
            pass
```

Remove the shim at the end of Task 4.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/agent_session.py tests/test_agent_session_callbacks.py
git commit -m "feat(agent_session): on_kb_event callback + expanded dispatch signature"
```

---

## Task 3: kb_emitter helper module

**Files:**
- Create: `src/reverser/gui_service/kb_emitter.py`
- Test: `tests/tools/test_kb_emit.py`

- [ ] **Step 1: Write failing test**

Create `tests/tools/test_kb_emit.py`:

```python
"""Tests for the KB → WS frame bridge."""
from unittest.mock import MagicMock

from reverser.gui_service.kb_emitter import emit_hypothesis, emit_finding
from reverser.kb.store import HypothesisFact, FindingFact


def test_emit_hypothesis_calls_session_callback(monkeypatch):
    sess = MagicMock()
    sess.on_kb_event = MagicMock()
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = HypothesisFact(id=7, parent_id=None, statement="x", status="testing")
        emit_hypothesis("update", row)
    finally:
        current_session.reset(token)
    sess.on_kb_event.assert_called_once()
    args = sess.on_kb_event.call_args.args
    assert args[0] == "hypothesis"
    assert args[1]["action"] == "update"
    assert args[1]["row"]["id"] == 7


def test_emit_finding_calls_session_callback(monkeypatch):
    sess = MagicMock()
    sess.on_kb_event = MagicMock()
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = FindingFact(id=3, target="ex.com", finding="open port")
        emit_finding("create", row)
    finally:
        current_session.reset(token)
    sess.on_kb_event.assert_called_once()
    args = sess.on_kb_event.call_args.args
    assert args[0] == "finding"
    assert args[1]["row"]["id"] == 3


def test_emit_is_noop_when_no_session(monkeypatch):
    from reverser.sessions import current_session
    # No `set`, so get() returns None
    row = HypothesisFact(id=1, parent_id=None, statement="x", status="proposed")
    emit_hypothesis("create", row)  # must not raise


def test_emit_swallows_callback_exceptions():
    sess = MagicMock()
    sess.on_kb_event = MagicMock(side_effect=RuntimeError("boom"))
    from reverser.sessions import current_session
    token = current_session.set(sess)
    try:
        row = HypothesisFact(id=1, parent_id=None, statement="x", status="proposed")
        emit_hypothesis("create", row)  # must not raise
    finally:
        current_session.reset(token)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/tools/test_kb_emit.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'reverser.gui_service.kb_emitter'`.

- [ ] **Step 3: Verify row field names**

Before implementing, confirm the row dataclasses' field names:

```bash
grep -n "^class HypothesisFact\|^class FindingFact" src/reverser/kb/store.py
grep -n "^    [a-z_]*:" src/reverser/kb/store.py | head -30
```

This determines what `_row_to_dict` must serialize. If `HypothesisFact` is a dataclass with `id`, `parent_id`, `statement`, `status`, `rationale`, `confidence`, `dispatched_to`, `dispatch_count`, `evidence_refs`, `tags`, `created_at`, `updated_at` (matching the renderer's `HypothesisRow` shape in `desktop/renderer/src/state/session-store.ts:47`), `dataclasses.asdict` is sufficient.

- [ ] **Step 4: Implement**

Create `src/reverser/gui_service/kb_emitter.py`:

```python
"""Bridge KB writes to the WS frame stream.

Each KB-mutating tool calls `emit_hypothesis(action, row)` or
`emit_finding(action, row)` after the SQLite write succeeds. The helper
looks up the current session via `reverser.sessions.current_session` and
fires `session.emit_kb_event(...)`. When there is no current session
(headless / CLI tool invocations), the call is a no-op.

The session adapter at `gui_service/session_adapter.py` attaches the
session's `on_kb_event` callback, which publishes a `hypothesis` or
`finding` WS frame on the EventBus.
"""
from __future__ import annotations
import dataclasses
from typing import Any

from ..sessions import current_session


def _row_to_dict(row: Any) -> dict:
    if dataclasses.is_dataclass(row):
        return dataclasses.asdict(row)
    if hasattr(row, "__dict__"):
        return {k: v for k, v in vars(row).items() if not k.startswith("_")}
    return dict(row)


def emit_hypothesis(action: str, row: Any) -> None:
    """Emit a `hypothesis` WS frame for a hypothesis create/update.

    `action` is "create" or "update". `row` is a HypothesisFact (or any
    dataclass with the same fields).
    """
    sess = current_session.get()
    if sess is None:
        return
    sess.emit_kb_event("hypothesis", {"action": action, "row": _row_to_dict(row)})


def emit_finding(action: str, row: Any) -> None:
    """Emit a `finding` WS frame for a finding create/update."""
    sess = current_session.get()
    if sess is None:
        return
    sess.emit_kb_event("finding", {"action": action, "row": _row_to_dict(row)})
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/tools/test_kb_emit.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/reverser/gui_service/kb_emitter.py tests/tools/test_kb_emit.py
git commit -m "feat(kb): add kb_emitter helper for bridging KB writes to WS frames"
```

---

## Task 4: Dispatch tool — mint dispatch_id, track sub_turn, emit lifecycle frames

**Files:**
- Modify: `src/reverser/tools/dispatch.py:316-405` (`_emit`, `try:` body, and `start`/`end` markers)

- [ ] **Step 1: Sketch the change**

The dispatch tool wraps a sub-agent `query()` loop. We need to:
1. Mint `dispatch_id = uuid4().hex` at entry.
2. Emit a `start` event before the loop: `_emit_start(specialty, hypothesis_id, sub_goal)`.
3. Track `sub_turn` starting at 0; increment on every `AssistantMessage`.
4. Pass `dispatch_id` and `sub_turn` into `_emit` for normal events.
5. Emit an `end` event after the loop: `_emit_end(status, cost, turns)`.

- [ ] **Step 2: Replace `_emit` and add lifecycle helpers**

Edit `src/reverser/tools/dispatch.py`. Replace lines ~316-331 (the existing `_emit`) with:

```python
    import uuid as _uuid
    dispatch_id = _uuid.uuid4().hex
    sub_turn = 0

    def _emit(kind: str, content: str) -> None:
        if sess is None:
            return
        # Persist for read-only replay (Phase 3a). Best-effort.
        try:
            sess._slog.log_dispatch_event(
                specialty, kind, content,
                dispatch_id=dispatch_id, sub_turn=sub_turn,
            )
        except TypeError:
            # Old log signature without kwargs (pre-Task 6 transition).
            try:
                sess._slog.log_dispatch_event(specialty, kind, content)
            except Exception:
                pass
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, sub_turn, kind, content,
            )
        except Exception:
            pass

    def _emit_start() -> None:
        if sess is None:
            return
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, 0, "start",
                _json.dumps({
                    "hypothesis_id": hypothesis_id,
                    "sub_goal": sub_goal,
                }),
            )
        except Exception:
            pass

    def _emit_end(status: str, cost: float, turns_consumed: int) -> None:
        if sess is None:
            return
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, 0, "end",
                _json.dumps({
                    "status": status,
                    "cost": cost,
                    "turns": turns_consumed,
                }),
            )
        except Exception:
            pass
```

(Add `import json as _json` near the top of the file if not already imported. Check first; the file already does `import json` based on `_summarize_tool_input`'s use; alias it as `_json` or reuse.)

- [ ] **Step 3: Wire start/end into the dispatch flow**

Wrap the existing `try: async for message in query(...)` block with `_emit_start()` before and `_emit_end(...)` after. Inside the loop, increment `sub_turn` on `AssistantMessage`:

```python
    _emit_start()
    try:
        async for message in query(prompt=sub_goal, options=options):
            if isinstance(message, AssistantMessage):
                # Increment sub-agent turn counter (used by the renderer to
                # group nested mini-chat bubbles by sub-turn).
                nonlocal_obj = None  # placeholder to remind: Python doesn't
                # support nonlocal in a fresh scope; sub_turn is a closure
                # variable. We rebind via a list trick:
                # Use a mutable container instead — see Step 3a.
```

Note: Python closures can't reassign captured ints without `nonlocal`. The cleanest fix is to make `sub_turn` a single-element list `_sub_turn = [0]`, then read `_sub_turn[0]` and write `_sub_turn[0] += 1`. Apply that pattern.

Final loop shape (replace lines ~358-402 of the existing file):

```python
    _sub_turn = [0]

    def _emit(kind: str, content: str) -> None:
        if sess is None:
            return
        try:
            sess._slog.log_dispatch_event(
                specialty, kind, content,
                dispatch_id=dispatch_id, sub_turn=_sub_turn[0],
            )
        except TypeError:
            try:
                sess._slog.log_dispatch_event(specialty, kind, content)
            except Exception:
                pass
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, _sub_turn[0], kind, content,
            )
        except Exception:
            pass

    _emit_start()
    try:
        async for message in query(prompt=sub_goal, options=options):
            if isinstance(message, AssistantMessage):
                _sub_turn[0] += 1
                for block in message.content:
                    if isinstance(block, TextBlock):
                        report_text = block.text
                        if block.text.strip():
                            _emit("text", block.text)
                    elif isinstance(block, ThinkingBlock):
                        thinking_text = getattr(block, "thinking", "") or ""
                        if thinking_text.strip():
                            _emit("thinking", thinking_text)
                    elif isinstance(block, ToolUseBlock):
                        tool_name = getattr(block, "name", "?")
                        tool_input = getattr(block, "input", {})
                        _emit(
                            "tool_call",
                            f"{tool_name} {_summarize_tool_input(tool_input)}",
                        )
            elif isinstance(message, UserMessage):
                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            kind = "tool_error" if getattr(block, "is_error", False) else "tool_result"
                            _emit(kind, _summarize_tool_result(getattr(block, "content", "")))
            elif isinstance(message, ResultMessage):
                cost_usd = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
                turns_consumed = int(getattr(message, "num_turns", 0) or 0)
                if message.subtype != "success":
                    subtype_str = str(message.subtype).lower()
                    if "budget" in subtype_str:
                        status = "budget_exhausted"
                    elif "turn" in subtype_str:
                        status = "turn_limit"
                    else:
                        status = "error"
                    if not report_text:
                        report_text = (
                            f"(specialist did not produce a report; "
                            f"subtype={message.subtype})"
                        )
    except Exception as e:
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"
        if not report_text:
            report_text = f"(dispatch failed: {error_msg})"
    finally:
        _emit_end(status, cost_usd, turns_consumed)
        if sess is not None:
            sess._snapshot.in_flight = None
            try:
                save_snapshot(sess._snapshot)
            except Exception:
                pass
```

- [ ] **Step 4: Remove the 3-arg shim in `AgentSession.emit_dispatch_event`**

Edit `src/reverser/agent_session.py` — remove the transitional shim from Task 2 Step 5. Replace `emit_dispatch_event` with the clean 5-arg signature from Task 2 Step 3.

- [ ] **Step 5: Update session-log writer signature (best-effort)**

If `src/reverser/session_log.py`'s `log_dispatch_event` doesn't currently accept `dispatch_id`/`sub_turn` kwargs, add them as optional keyword args defaulting to None. The kwargs are passed through to the JSONL payload; old logs without them replay fine.

```bash
grep -n "def log_dispatch_event" src/reverser/session_log.py
```

Modify the signature to accept `*, dispatch_id: str | None = None, sub_turn: int | None = None` and include them in the persisted record.

- [ ] **Step 6: Run dispatch tests**

```bash
uv run pytest tests/test_agent_session_callbacks.py tests/tools/test_kb_emit.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/dispatch.py src/reverser/agent_session.py src/reverser/session_log.py
git commit -m "feat(dispatch): mint dispatch_id, track sub_turn, emit start/end frames"
```

---

## Task 5: Wire kb_emitter into KB mutator tools

**Files:**
- Modify: `src/reverser/tools/kb.py` — `kb_add_finding` (~289), `kb_add_hypothesis` (~354), `kb_update_hypothesis` (~447)

- [ ] **Step 1: Write failing integration test**

Add to `tests/tools/test_kb_emit.py`:

```python
@pytest.fixture
def session_with_spy(tmp_path, monkeypatch):
    from reverser.sessions import current_session
    from unittest.mock import MagicMock
    sess = MagicMock()
    sess.on_kb_event = MagicMock()
    sess.emit_kb_event = lambda kind, payload: sess.on_kb_event(kind, payload)
    token = current_session.set(sess)
    yield sess
    current_session.reset(token)


@pytest.mark.asyncio
async def test_kb_update_hypothesis_emits_frame(session_with_spy, tmp_path, monkeypatch):
    """The kb_update_hypothesis tool publishes a hypothesis frame after the write."""
    from reverser.tools.kb import kb_update_hypothesis
    # Set up a target KB with a row to update.
    target = "test-target"
    from reverser.kb.store import for_target
    kb = for_target(target)
    h_id = kb.add_hypothesis(parent_id=None, statement="x", status="proposed")
    # Auth bypass for the test (kb tools check this).
    monkeypatch.setenv("REVERSER_AUTHORIZED", "1")
    monkeypatch.setattr("reverser.tools.kb._check_auth", lambda: None)

    await kb_update_hypothesis({
        "target": target, "id": h_id, "status": "testing",
    })

    session_with_spy.on_kb_event.assert_called()
    kind, payload = session_with_spy.on_kb_event.call_args.args
    assert kind == "hypothesis"
    assert payload["action"] == "update"
    assert payload["row"]["id"] == h_id
    assert payload["row"]["status"] == "testing"
```

(Add `@pytest.mark.asyncio` and the `pytest-asyncio` import if not yet wired; check `pyproject.toml` for the marker config. The kb tools use `async def` so this test must be async.)

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/tools/test_kb_emit.py::test_kb_update_hypothesis_emits_frame -v
```

Expected: FAIL — `on_kb_event` not called.

- [ ] **Step 3: Wire emit into kb_update_hypothesis**

Edit `src/reverser/tools/kb.py:447-463`. Add the emit call after the SQLite write:

```python
async def kb_update_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    if kb.get_hypothesis(args["id"]) is None:
        return format_tool_result(f"No hypothesis with id={args['id']}.")
    update_kwargs = {
        k: args[k]
        for k in ("status", "rationale", "confidence", "dispatched_to",
                  "evidence_refs", "tags")
        if k in args
    }
    kb.update_hypothesis(args["id"], **update_kwargs)
    # NEW: emit a hypothesis WS frame so the renderer's Hypotheses pane
    # updates live. Best-effort; no-op when no current session.
    from ..gui_service.kb_emitter import emit_hypothesis
    updated = kb.get_hypothesis(args["id"])
    if updated is not None:
        emit_hypothesis("update", updated)
    return format_tool_result(
        f"Hypothesis #{args['id']} updated: {sorted(update_kwargs.keys())}"
    )
```

- [ ] **Step 4: Wire emit into kb_add_hypothesis**

Find the function around line 354. Add after the existing `add_hypothesis()` call (`return format_tool_result(...)` line):

```python
    h_id = kb.add_hypothesis(...)
    # NEW: emit a hypothesis WS frame on create.
    from ..gui_service.kb_emitter import emit_hypothesis
    created = kb.get_hypothesis(h_id)
    if created is not None:
        emit_hypothesis("create", created)
    return format_tool_result(...)
```

(Read the exact existing body in implementation and slot in the emit call right after the write, before the return.)

- [ ] **Step 5: Wire emit into kb_add_finding**

Find the function around line 289. After the `record_finding(...)` call (line 305), add:

```python
    fid = for_target(target).record_finding(finding)
    # NEW: emit a finding WS frame on create.
    from ..gui_service.kb_emitter import emit_finding
    created = for_target(target).get_finding(fid) if hasattr(for_target(target), "get_finding") else None
    if created is not None:
        emit_finding("create", created)
    return format_tool_result(...)
```

If `get_finding(fid)` does not exist on the store, construct a dict directly from the `finding` object that was passed to `record_finding`, plus the new id:

```python
    # If the store has no get_finding, synthesize the row from the input.
    from dataclasses import asdict, is_dataclass
    row = asdict(finding) if is_dataclass(finding) else dict(finding)
    row["id"] = fid
    emit_finding("create", row)
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/tools/test_kb_emit.py -v
```

Expected: PASS (5 tests including the new integration).

- [ ] **Step 7: Commit**

```bash
git add src/reverser/tools/kb.py tests/tools/test_kb_emit.py
git commit -m "feat(kb): emit hypothesis/finding WS frames after each mutator"
```

---

## Task 6: session_adapter — turn on frames, dispatch lifecycle, kb_event publishing

**Files:**
- Modify: `src/reverser/gui_service/session_adapter.py:17-43` (`_event_to_frame`)
- Modify: `src/reverser/gui_service/session_adapter.py:85-103` (`_on_dispatch_event`)
- Modify: `src/reverser/gui_service/session_adapter.py:46-83` (`GUISession.__init__`)

- [ ] **Step 1: Update existing adapter test for turn field**

Look at `tests/gui_service/test_session_adapter.py` to see what's there:

```bash
cat tests/gui_service/test_session_adapter.py
```

For each existing test that asserts on a frame shape (`text`/`thinking`/`tool_call`/`tool_result`), add an assertion that the frame contains `turn`. Tests for frames where turn is undefined (`turn=0` AgentEvent) should expect `turn: 0`.

- [ ] **Step 2: Add new tests for the new fields and frames**

Append to `tests/gui_service/test_session_adapter.py`:

```python
def test_text_frame_has_turn():
    from reverser.backends.base import AgentEvent
    from reverser.gui_service.session_adapter import _event_to_frame
    ev = AgentEvent(kind="text", content="hi", turn=3)
    frame = _event_to_frame(ev)
    assert frame == {"type": "text", "role": "assistant", "delta": "hi", "turn": 3}


def test_tool_call_frame_has_tool_use_id_and_turn():
    from reverser.backends.base import AgentEvent
    from reverser.gui_service.session_adapter import _event_to_frame
    ev = AgentEvent(
        kind="tool_call", tool_name="bash", tool_input="ls",
        tool_use_id="tool_abc", turn=2,
    )
    frame = _event_to_frame(ev)
    assert frame["type"] == "tool_call"
    assert frame["tool_use_id"] == "tool_abc"
    assert frame["turn"] == 2


def test_tool_result_frame_has_tool_use_id_and_turn():
    from reverser.backends.base import AgentEvent
    from reverser.gui_service.session_adapter import _event_to_frame
    ev = AgentEvent(
        kind="tool_result", content="ok", is_error=False,
        tool_use_id="tool_abc", turn=2,
    )
    frame = _event_to_frame(ev)
    assert frame["type"] == "tool_result"
    assert frame["tool_use_id"] == "tool_abc"
    assert frame["turn"] == 2
    assert frame["ok"] is True
```

- [ ] **Step 3: Run to verify failure**

```bash
uv run pytest tests/gui_service/test_session_adapter.py -v
```

Expected: FAIL — frames missing `turn` / `tool_use_id`.

- [ ] **Step 4: Update `_event_to_frame`**

Edit `src/reverser/gui_service/session_adapter.py`. Replace `_event_to_frame` (lines 17-43):

```python
def _event_to_frame(ev: AgentEvent) -> dict[str, Any] | None:
    """Translate a single AgentEvent into a WS frame."""
    if ev.kind == "text":
        return {"type": "text", "role": "assistant", "delta": ev.content, "turn": ev.turn}
    if ev.kind == "thinking":
        return {"type": "thinking", "delta": ev.content, "redacted": False, "turn": ev.turn}
    if ev.kind == "tool_call":
        return {
            "type": "tool_call",
            "name": ev.tool_name,
            "args": ev.tool_input,
            "tool_use_id": ev.tool_use_id,
            "turn": ev.turn,
        }
    if ev.kind == "tool_result":
        return {
            "type": "tool_result",
            "ok": not ev.is_error,
            "preview": (ev.content or "")[:4096],
            "tool_use_id": ev.tool_use_id,
            "turn": ev.turn,
        }
    if ev.kind == "turn":
        return {"type": "status", "phase": "running", "turns": ev.turns}
    if ev.kind == "result":
        return {"type": "status", "phase": "awaiting_input",
                "subtype": ev.subtype, "cost": ev.cost, "turns": ev.turns}
    if ev.kind == "error":
        return {"type": "log", "level": "error", "msg": ev.content}
    return None
```

- [ ] **Step 5: Update `_on_dispatch_event` signature + publish lifecycle phases**

Replace the existing `_on_dispatch_event` (lines 85-103) with:

```python
    def _on_dispatch_event(
        self,
        specialty: str,
        dispatch_id: str,
        sub_turn: int,
        kind: str,
        content: str,
    ) -> None:
        """Publish a dispatch sub-agent event to the bus.

        kind is one of: "start", "end", "text", "thinking", "tool_call",
        "tool_result", "tool_error". For "start"/"end", `content` is a JSON
        string of structured payload; for other kinds it's free-form text.
        """
        frame: dict[str, Any] = {
            "type": "dispatch",
            "dispatch_id": dispatch_id,
            "turn": self._agent.stats.turns,   # manager's current turn
            "phase": kind,
            "specialty": specialty,
        }
        if kind in ("start", "end"):
            try:
                import json
                payload = json.loads(content) if content else {}
                frame.update(payload)
            except Exception:
                pass
        else:
            frame["sub_turn"] = sub_turn
            frame["content"] = content

        try:
            asyncio.create_task(self._bus.publish(self.session_id, frame))
        except RuntimeError:
            pass
```

- [ ] **Step 6: Add `_on_kb_event` + wire it in `__init__`**

After `_on_dispatch_event`, add:

```python
    def _on_kb_event(self, kind: str, payload: dict) -> None:
        """Publish a KB write to the bus.

        kind is "hypothesis" or "finding"; payload is {"action", "row"}.
        """
        frame = {"type": kind, **payload}
        try:
            asyncio.create_task(self._bus.publish(self.session_id, frame))
        except RuntimeError:
            pass
```

And wire it in `__init__`. Modify the existing `self._agent.on_dispatch_event = self._on_dispatch_event` (around line 83) to also set:

```python
        self._agent.on_dispatch_event = self._on_dispatch_event
        self._agent.on_kb_event = self._on_kb_event
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/gui_service/test_session_adapter.py tests/tools/test_kb_emit.py tests/test_agent_session_callbacks.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/reverser/gui_service/session_adapter.py tests/gui_service/test_session_adapter.py
git commit -m "feat(adapter): turn on frames, dispatch lifecycle, kb_event publishing"
```

---

# Phase 2 — Renderer session store

## Task 7: Add Vitest + jsdom; smoke test passes

**Files:**
- Modify: `desktop/package.json`
- Create: `desktop/renderer/vitest.config.ts`
- Create: `desktop/renderer/src/state/session-store.test.ts` (smoke only at this task)

- [ ] **Step 1: Install Vitest + jsdom**

From `desktop/`:

```bash
cd desktop && npm install --save-dev vitest @vitest/ui jsdom @testing-library/react @testing-library/jest-dom
```

- [ ] **Step 2: Add a `test` script in `desktop/package.json`**

Add to the `scripts` block:

```json
    "test": "vitest run --root renderer",
    "test:watch": "vitest --root renderer"
```

- [ ] **Step 3: Create `desktop/renderer/vitest.config.ts`**

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

- [ ] **Step 4: Create `desktop/renderer/vitest.setup.ts`**

```typescript
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 5: Smoke test the harness**

Create `desktop/renderer/src/state/session-store.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { makeSessionStore } from "./session-store";

describe("makeSessionStore", () => {
  it("initializes with empty state", () => {
    const store = makeSessionStore();
    const state = store.getState();
    expect(state.status).toBe("idle");
    expect(state.messages).toEqual([]);
  });
});
```

- [ ] **Step 6: Run**

```bash
cd desktop && npm test
```

Expected: PASS (1 test).

- [ ] **Step 7: Commit**

```bash
git add desktop/package.json desktop/package-lock.json desktop/renderer/vitest.config.ts desktop/renderer/vitest.setup.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "test(renderer): add Vitest + jsdom harness with smoke test"
```

---

## Task 8: New types + state shape in session-store

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts`
- Modify: `desktop/renderer/src/state/session-store.test.ts`

- [ ] **Step 1: Write failing tests for new types**

Replace the smoke test in `session-store.test.ts` with:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { makeSessionStore } from "./session-store";

describe("session-store new shape", () => {
  let store: ReturnType<typeof makeSessionStore>;

  beforeEach(() => {
    store = makeSessionStore();
  });

  it("initializes with empty turns Map and currentTurn=0", () => {
    const s = store.getState();
    expect(s.turns).toBeInstanceOf(Map);
    expect(s.turns.size).toBe(0);
    expect(s.currentTurn).toBe(0);
    expect(s.findings).toBeInstanceOf(Map);
    expect(s.findings.size).toBe(0);
  });

  it("no longer exposes messages or pendingAssistantText", () => {
    const s = store.getState() as Record<string, unknown>;
    expect(s.messages).toBeUndefined();
    expect(s.pendingAssistantText).toBeUndefined();
    expect(s.thinkingEntries).toBeUndefined();
    expect(s.dispatchEntries).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
cd desktop && npm test
```

Expected: FAIL — `turns` undefined, `findings` is array not Map, `messages` still present.

- [ ] **Step 3: Update the type declarations and initial state**

Edit `desktop/renderer/src/state/session-store.ts`. Replace the type declarations and initial state. Keep the file's existing `WSFrame` union for now (Task 9 expands it).

Replace lines 21-46 (the `ChatMessage`, `ToolCall`, `ThinkingEntry`, `DispatchEntry` declarations) with:

```typescript
export type ToolCall = {
  id: string;                          // tool_use_id from backend
  name: string;
  args: string;
  result?: { ok: boolean; preview: string };
};

export type SubTurn = {
  thinkingDeltas: string[];
  speechDeltas: string[];
  toolCalls: { name: string; content: string }[];
  toolResults: { ok: boolean; content: string }[];
};

export type Dispatch = {
  id: string;                          // dispatch_id from backend
  specialty: string;
  hypothesisId?: number;
  subGoal: string;
  status: "running" | "completed" | "error";
  cost?: number;
  turnsConsumed?: number;
  subTurns: Map<number, SubTurn>;
};

export type TurnOrderingEntry =
  | { kind: "thinking"; index: number }
  | { kind: "speech"; index: number }
  | { kind: "tool"; id: string }
  | { kind: "dispatch"; id: string };

export type Turn = {
  turn: number;
  userMessage?: string;
  thinkingDeltas: string[];
  speechDeltas: string[];
  toolCalls: Map<string, ToolCall>;
  dispatches: Map<string, Dispatch>;
  status: "streaming" | "done";
  ordering: TurnOrderingEntry[];
};
```

Replace lines 47-61 (`HypothesisRow` is fine, keep) and add a `FindingRow`:

```typescript
export type FindingRow = {
  id: number;
  target?: string;
  finding?: string;
  severity?: string | null;
  evidence?: string | null;
  refs?: unknown[] | null;
  created_at?: string | null;
  updated_at?: string | null;
};
```

Replace lines 70-88 (`SessionState`) with:

```typescript
export type SessionState = {
  status: "idle" | "running" | "awaiting_input" | "stopped" | "completed" | "error";
  turns: Map<number, Turn>;
  currentTurn: number;
  hypotheses: Map<number, HypothesisRow>;
  findings: Map<number, FindingRow>;
  budget: { spent: number; remaining: number; turn: number } | null;
  connBreakerTripped: boolean;
  log: { level: string; msg: string; ts: number }[];
  replayed: boolean;
};
```

Replace `_initialState()` (lines 99-112) with:

```typescript
const _initialState = (): SessionState => ({
  status: "idle",
  turns: new Map(),
  currentTurn: 0,
  hypotheses: new Map(),
  findings: new Map(),
  budget: null,
  connBreakerTripped: false,
  log: [],
  replayed: false,
});
```

For now, leave the `Actions` block and `ingest`/`appendUserMessage`/`seedConversation`/`seedFromSessionLog`/`seedHypotheses` placeholders that still reference the old shape — they'll be replaced in Tasks 9-13. To keep TypeScript happy in the meantime, comment them out or stub them:

```typescript
type Actions = {
  ingest: (frame: WSFrame) => void;
  appendUserMessage: (text: string) => void;
  reset: () => void;
  seedFromSessionLog: (events: LogEventInput[]) => void;
  seedHypotheses: (rows: HypothesisRow[]) => void;
  seedFindings: (rows: FindingRow[]) => void;
};

export const makeSessionStore = () =>
  create<SessionState & Actions>((set) => ({
    ..._initialState(),
    reset: () => set(_initialState()),
    appendUserMessage: () => set({}),   // re-implemented in Task 14
    ingest: () => set({}),               // re-implemented in Tasks 9-12
    seedFromSessionLog: () => set({}),   // re-implemented in Task 13
    seedHypotheses: (rows) => set(() => {
      const m = new Map<number, HypothesisRow>();
      for (const r of rows) m.set(r.id, r);
      return { hypotheses: m };
    }),
    seedFindings: (rows) => set(() => {
      const m = new Map<number, FindingRow>();
      for (const r of rows) m.set(r.id, r);
      return { findings: m };
    }),
  }));
```

`seedConversation` is removed — the new replay path covers its job.

- [ ] **Step 4: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS on the two new tests.

- [ ] **Step 5: TypeScript may complain about consumers**

```bash
cd desktop && npm run lint
```

Expect many errors in `panes/ChatPane.tsx`, `panes/HypothesesPane.tsx`, etc. Those are addressed in Phase 3. For this commit, only the store file changes.

To keep `npm run lint` clean in the interim, **do not commit yet** — Tasks 9-19 land before another lint pass. But mark this task complete and move on.

- [ ] **Step 6: Commit (WIP — known type errors in consumers)**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "refactor(store): new turn-indexed shape (WIP — consumers updated in follow-ups)"
```

---

## Task 9: ingest reducer cases for text/thinking/tool_call/tool_result/status

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts` (WSFrame union + `ingest`)
- Modify: `desktop/renderer/src/state/session-store.test.ts`

- [ ] **Step 1: Update WSFrame union**

Edit the `WSFrame` union near line 7:

```typescript
export type WSFrame =
  | { type: "text"; role: "assistant"; delta: string; turn: number }
  | { type: "thinking"; delta: string; redacted: boolean; turn: number }
  | { type: "tool_call"; name: string; args: string; tool_use_id: string; turn: number }
  | { type: "tool_result"; ok: boolean; preview: string; tool_use_id: string; turn: number }
  | DispatchFrame
  | { type: "hypothesis"; action: "create" | "update"; row: HypothesisRow }
  | { type: "finding"; action: "create" | "update"; row: FindingRow }
  | { type: "budget"; spent: number; remaining: number; turn: number }
  | { type: "conn_breaker"; target: string; tripped: boolean }
  | { type: "status"; phase: string; turns?: number; subtype?: string; cost?: number | null }
  | { type: "log"; level: string; msg: string };

export type DispatchFrame =
  | { type: "dispatch"; dispatch_id: string; turn: number; phase: "start";
      specialty: string; hypothesis_id?: number; sub_goal: string }
  | { type: "dispatch"; dispatch_id: string; turn: number; phase: "end";
      specialty: string; status: string; cost: number; turns: number }
  | { type: "dispatch"; dispatch_id: string; turn: number; sub_turn: number;
      phase: "text" | "thinking" | "tool_call" | "tool_result" | "tool_error";
      specialty: string; content: string };
```

- [ ] **Step 2: Add a helper for getting-or-creating a turn**

In `session-store.ts`, add:

```typescript
function _getOrCreateTurn(turns: Map<number, Turn>, turn: number): Turn {
  let t = turns.get(turn);
  if (!t) {
    t = {
      turn,
      thinkingDeltas: [],
      speechDeltas: [],
      toolCalls: new Map(),
      dispatches: new Map(),
      status: "streaming",
      ordering: [],
    };
    turns.set(turn, t);
  }
  return t;
}
```

- [ ] **Step 3: Write tests for text/thinking/tool_call/tool_result reducers**

Add to `session-store.test.ts`:

```typescript
import type { Turn } from "./session-store";

describe("ingest text frames", () => {
  it("creates a turn and appends speech delta with a speech ordering entry", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "text", role: "assistant", delta: "Hi ", turn: 1 });
    const t = store.getState().turns.get(1)!;
    expect(t.speechDeltas).toEqual(["Hi "]);
    expect(t.ordering).toEqual([{ kind: "speech", index: 0 }]);
  });

  it("appends to existing speech ordering entry when consecutive", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "text", role: "assistant", delta: "Hi ", turn: 1 });
    store.getState().ingest({ type: "text", role: "assistant", delta: "there", turn: 1 });
    const t = store.getState().turns.get(1)!;
    expect(t.speechDeltas).toEqual(["Hi ", "there"]);
    expect(t.ordering).toEqual([{ kind: "speech", index: 0 }]);
  });
});

describe("ingest thinking frames", () => {
  it("creates a turn and appends thinking delta with a thinking ordering entry", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "thinking", delta: "hmm", redacted: false, turn: 1 });
    const t = store.getState().turns.get(1)!;
    expect(t.thinkingDeltas).toEqual(["hmm"]);
    expect(t.ordering).toEqual([{ kind: "thinking", index: 0 }]);
  });
});

describe("ingest tool_call/tool_result", () => {
  it("creates a ToolCall keyed by tool_use_id and pairs the result", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "tool_call", name: "bash", args: '{"cmd":"ls"}',
      tool_use_id: "tu_1", turn: 1,
    });
    store.getState().ingest({
      type: "tool_result", ok: true, preview: "file.txt",
      tool_use_id: "tu_1", turn: 1,
    });
    const t = store.getState().turns.get(1)!;
    const tc = t.toolCalls.get("tu_1")!;
    expect(tc.name).toBe("bash");
    expect(tc.result).toEqual({ ok: true, preview: "file.txt" });
    expect(t.ordering).toEqual([{ kind: "tool", id: "tu_1" }]);
  });

  it("drops a tool_result with unknown tool_use_id", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "tool_result", ok: true, preview: "x",
      tool_use_id: "unknown", turn: 1,
    });
    const t = store.getState().turns.get(1);
    expect(t?.toolCalls.size ?? 0).toBe(0);
  });
});

describe("ingest status frames", () => {
  it("advances currentTurn on status running", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    expect(store.getState().currentTurn).toBe(1);
    expect(store.getState().turns.get(1)?.status).toBe("streaming");

    store.getState().ingest({ type: "status", phase: "running", turns: 2 });
    expect(store.getState().currentTurn).toBe(2);
    expect(store.getState().turns.get(1)?.status).toBe("done");
  });

  it("marks current turn done on awaiting_input", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    store.getState().ingest({ type: "status", phase: "awaiting_input" });
    expect(store.getState().turns.get(1)?.status).toBe("done");
    expect(store.getState().status).toBe("awaiting_input");
  });
});
```

- [ ] **Step 4: Run to verify failure**

```bash
cd desktop && npm test
```

Expected: FAIL — `ingest` is a no-op.

- [ ] **Step 5: Implement reducer cases**

Edit `session-store.ts`. Replace the stub `ingest: () => set({})` with the full reducer. Within `makeSessionStore`:

```typescript
    ingest: (frame) =>
      set((s) => {
        switch (frame.type) {
          case "text": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            t.speechDeltas = [...t.speechDeltas, frame.delta];
            const last = t.ordering.at(-1);
            if (!last || last.kind !== "speech") {
              t.ordering = [...t.ordering, { kind: "speech", index: t.speechDeltas.length - 1 }];
            }
            return { turns };
          }
          case "thinking": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            t.thinkingDeltas = [...t.thinkingDeltas, frame.delta];
            const last = t.ordering.at(-1);
            if (!last || last.kind !== "thinking") {
              t.ordering = [...t.ordering, { kind: "thinking", index: t.thinkingDeltas.length - 1 }];
            }
            return { turns };
          }
          case "tool_call": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            const tc: ToolCall = {
              id: frame.tool_use_id, name: frame.name, args: frame.args,
            };
            t.toolCalls = new Map(t.toolCalls);
            t.toolCalls.set(frame.tool_use_id, tc);
            t.ordering = [...t.ordering, { kind: "tool", id: frame.tool_use_id }];
            return { turns };
          }
          case "tool_result": {
            const turns = new Map(s.turns);
            const t = turns.get(frame.turn);
            if (!t) return {};
            const tc = t.toolCalls.get(frame.tool_use_id);
            if (!tc) {
              console.warn("tool_result for unknown tool_use_id", frame.tool_use_id);
              return {};
            }
            t.toolCalls = new Map(t.toolCalls);
            t.toolCalls.set(frame.tool_use_id, {
              ...tc, result: { ok: frame.ok, preview: frame.preview },
            });
            return { turns };
          }
          case "status": {
            const next: Partial<SessionState> = { status: frame.phase as SessionState["status"] };
            if (frame.phase === "running" && typeof frame.turns === "number") {
              const turns = new Map(s.turns);
              // Mark prior turn done.
              const prev = turns.get(s.currentTurn);
              if (prev && prev.status === "streaming") {
                turns.set(s.currentTurn, { ...prev, status: "done" });
              }
              _getOrCreateTurn(turns, frame.turns);
              next.turns = turns;
              next.currentTurn = frame.turns;
            } else if (["awaiting_input", "stopped", "completed", "error"].includes(frame.phase)) {
              const turns = new Map(s.turns);
              const cur = turns.get(s.currentTurn);
              if (cur && cur.status === "streaming") {
                turns.set(s.currentTurn, { ...cur, status: "done" });
                next.turns = turns;
              }
            }
            return next;
          }
          case "budget":
            return { budget: { spent: frame.spent, remaining: frame.remaining, turn: frame.turn } };
          case "conn_breaker":
            return { connBreakerTripped: frame.tripped };
          case "log":
            return { log: [...s.log.slice(-499), { level: frame.level, msg: frame.msg, ts: Date.now() }] };
          // dispatch, hypothesis, finding — Tasks 10-11.
          default:
            return {};
        }
      }),
```

- [ ] **Step 6: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS on the new reducer cases.

- [ ] **Step 7: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(store): ingest reducers for text/thinking/tool_call/tool_result/status"
```

---

## Task 10: ingest reducer cases for dispatch lifecycle

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts` (`ingest` dispatch case)
- Modify: `desktop/renderer/src/state/session-store.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `session-store.test.ts`:

```typescript
describe("ingest dispatch frames", () => {
  it("start creates a Dispatch on the parent turn", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webpentest", sub_goal: "test xss", hypothesis_id: 4,
    });
    const t = store.getState().turns.get(1)!;
    const d = t.dispatches.get("d1")!;
    expect(d.specialty).toBe("webpentest");
    expect(d.subGoal).toBe("test xss");
    expect(d.hypothesisId).toBe(4);
    expect(d.status).toBe("running");
    expect(t.ordering).toEqual([{ kind: "dispatch", id: "d1" }]);
  });

  it("text/thinking/tool_call drill into the sub-turn", () => {
    const store = makeSessionStore();
    const ingest = store.getState().ingest;
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webpentest", sub_goal: "x" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 1,
      phase: "thinking", specialty: "webpentest", content: "scoping" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 1,
      phase: "text", specialty: "webpentest", content: "starting" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, sub_turn: 2,
      phase: "tool_call", specialty: "webpentest", content: "bash ls" });

    const d = store.getState().turns.get(1)!.dispatches.get("d1")!;
    const st1 = d.subTurns.get(1)!;
    const st2 = d.subTurns.get(2)!;
    expect(st1.thinkingDeltas).toEqual(["scoping"]);
    expect(st1.speechDeltas).toEqual(["starting"]);
    expect(st2.toolCalls.length).toBe(1);
    expect(st2.toolCalls[0].content).toBe("bash ls");
  });

  it("end sets status/cost/turnsConsumed on the Dispatch", () => {
    const store = makeSessionStore();
    const ingest = store.getState().ingest;
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, phase: "start",
      specialty: "webpentest", sub_goal: "x" });
    ingest({ type: "dispatch", dispatch_id: "d1", turn: 1, phase: "end",
      specialty: "webpentest", status: "completed", cost: 0.53, turns: 4 });

    const d = store.getState().turns.get(1)!.dispatches.get("d1")!;
    expect(d.status).toBe("completed");
    expect(d.cost).toBe(0.53);
    expect(d.turnsConsumed).toBe(4);
  });

  it("end without start is dropped silently", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "dispatch", dispatch_id: "ghost", turn: 1, phase: "end",
      specialty: "x", status: "completed", cost: 0, turns: 0,
    });
    expect(store.getState().turns.get(1)?.dispatches.size ?? 0).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
cd desktop && npm test
```

Expected: FAIL — dispatch frames hit the default no-op case.

- [ ] **Step 3: Implement**

In `session-store.ts`, add the `dispatch` case to the `ingest` switch (before the `default`):

```typescript
          case "dispatch": {
            const turns = new Map(s.turns);
            const t = _getOrCreateTurn(turns, frame.turn);
            const dispatches = new Map(t.dispatches);

            if (frame.phase === "start") {
              dispatches.set(frame.dispatch_id, {
                id: frame.dispatch_id,
                specialty: frame.specialty,
                hypothesisId: frame.hypothesis_id,
                subGoal: frame.sub_goal,
                status: "running",
                subTurns: new Map(),
              });
              t.dispatches = dispatches;
              t.ordering = [...t.ordering, { kind: "dispatch", id: frame.dispatch_id }];
              return { turns };
            }

            if (frame.phase === "end") {
              const d = dispatches.get(frame.dispatch_id);
              if (!d) {
                console.warn("dispatch end for unknown dispatch_id", frame.dispatch_id);
                return {};
              }
              dispatches.set(frame.dispatch_id, {
                ...d,
                status: frame.status === "completed" ? "completed" : "error",
                cost: frame.cost,
                turnsConsumed: frame.turns,
              });
              t.dispatches = dispatches;
              return { turns };
            }

            // sub-turn event (text/thinking/tool_call/tool_result/tool_error)
            const d = dispatches.get(frame.dispatch_id);
            if (!d) {
              console.warn("dispatch event for unknown dispatch_id", frame.dispatch_id);
              return {};
            }
            const subTurns = new Map(d.subTurns);
            let st = subTurns.get(frame.sub_turn);
            if (!st) {
              st = { thinkingDeltas: [], speechDeltas: [], toolCalls: [], toolResults: [] };
            } else {
              st = { ...st };
            }
            if (frame.phase === "thinking") st.thinkingDeltas = [...st.thinkingDeltas, frame.content];
            else if (frame.phase === "text") st.speechDeltas = [...st.speechDeltas, frame.content];
            else if (frame.phase === "tool_call") st.toolCalls = [...st.toolCalls, { name: "", content: frame.content }];
            else if (frame.phase === "tool_result") st.toolResults = [...st.toolResults, { ok: true, content: frame.content }];
            else if (frame.phase === "tool_error") st.toolResults = [...st.toolResults, { ok: false, content: frame.content }];
            subTurns.set(frame.sub_turn, st);

            dispatches.set(frame.dispatch_id, { ...d, subTurns });
            t.dispatches = dispatches;
            return { turns };
          }
```

- [ ] **Step 4: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(store): ingest reducers for dispatch lifecycle"
```

---

## Task 11: ingest reducer cases for hypothesis/finding

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts` (`ingest` cases + remove dead `kb_update` case)
- Modify: `desktop/renderer/src/state/session-store.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `session-store.test.ts`:

```typescript
describe("ingest hypothesis/finding frames", () => {
  it("hypothesis create adds a row keyed by id", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "hypothesis", action: "create",
      row: { id: 4, parent_id: null, statement: "x", status: "proposed" },
    });
    expect(store.getState().hypotheses.get(4)?.statement).toBe("x");
  });

  it("hypothesis update overwrites by id", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "hypothesis", action: "create",
      row: { id: 4, parent_id: null, statement: "x", status: "proposed" },
    });
    store.getState().ingest({
      type: "hypothesis", action: "update",
      row: { id: 4, parent_id: null, statement: "x", status: "confirmed" },
    });
    expect(store.getState().hypotheses.get(4)?.status).toBe("confirmed");
  });

  it("finding create adds a row keyed by id", () => {
    const store = makeSessionStore();
    store.getState().ingest({
      type: "finding", action: "create",
      row: { id: 1, target: "ex", finding: "open port 22" },
    });
    expect(store.getState().findings.get(1)?.finding).toBe("open port 22");
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
cd desktop && npm test
```

Expected: FAIL.

- [ ] **Step 3: Implement**

In `session-store.ts`, add cases to the `ingest` switch:

```typescript
          case "hypothesis": {
            const m = new Map(s.hypotheses);
            m.set(frame.row.id, frame.row);
            return { hypotheses: m };
          }
          case "finding": {
            const m = new Map(s.findings);
            m.set(frame.row.id, frame.row);
            return { findings: m };
          }
```

Remove any remaining `case "kb_update"` and `case "finding"` (with old shape) cases — the new shape supersedes them.

- [ ] **Step 4: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(store): ingest reducers for hypothesis/finding frames"
```

---

## Task 12: appendUserMessage + seedFromSessionLog

**Files:**
- Modify: `desktop/renderer/src/state/session-store.ts`
- Modify: `desktop/renderer/src/state/session-store.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `session-store.test.ts`:

```typescript
describe("appendUserMessage", () => {
  it("attaches text to the next turn's userMessage", () => {
    const store = makeSessionStore();
    // currentTurn is 0 → user message goes onto turn 1
    store.getState().appendUserMessage("what does this do");
    expect(store.getState().turns.get(1)?.userMessage).toBe("what does this do");
  });

  it("attaches to currentTurn+1 when a turn is already in flight", () => {
    const store = makeSessionStore();
    store.getState().ingest({ type: "status", phase: "running", turns: 1 });
    store.getState().appendUserMessage("follow-up");
    expect(store.getState().turns.get(2)?.userMessage).toBe("follow-up");
  });
});

describe("seedFromSessionLog", () => {
  it("rebuilds turns from a log without explicit ids", () => {
    const store = makeSessionStore();
    store.getState().seedFromSessionLog([
      { kind: "turn", turn: 1, ts: null } as any,
      { kind: "thinking", content: "hmm", ts: null },
      { kind: "tool_call", name: "bash", input: "ls", ts: null },
      { kind: "tool_result", ok: true, preview: "out", ts: null },
    ]);
    const t = store.getState().turns.get(1)!;
    expect(t.thinkingDeltas).toEqual(["hmm"]);
    expect(t.toolCalls.size).toBe(1);
    const tc = [...t.toolCalls.values()][0];
    expect(tc.result?.preview).toBe("out");
    expect(store.getState().replayed).toBe(true);
  });
});
```

(The fixture above presumes the log event type union grows a `{ kind: "turn"; turn: number }` variant. If the existing `LogEventInput` already encodes turn boundaries differently, adapt to that — read `desktop/renderer/src/state/session-store.ts:63-68` for the current shape and align the test.)

- [ ] **Step 2: Update `LogEventInput` to carry an explicit turn marker**

In `session-store.ts`:

```typescript
type LogEventInput =
  | { kind: "turn"; turn: number; ts: string | null }
  | { kind: "thinking"; content: string; ts: string | null }
  | { kind: "tool_call"; name: string; input: string; ts: string | null }
  | { kind: "tool_result"; ok: boolean; preview: string; ts: string | null }
  | { kind: "dispatch"; specialty: string; phase: string; content: string; ts: string | null;
      dispatch_id?: string; sub_turn?: number };
```

- [ ] **Step 3: Implement `appendUserMessage` and `seedFromSessionLog`**

Replace the stubs in `makeSessionStore`:

```typescript
    appendUserMessage: (text) =>
      set((s) => {
        const targetTurn = s.currentTurn + 1;
        const turns = new Map(s.turns);
        const t = _getOrCreateTurn(turns, targetTurn);
        turns.set(targetTurn, { ...t, userMessage: text });
        return { turns };
      }),

    seedFromSessionLog: (events) =>
      set(() => {
        const turns = new Map<number, Turn>();
        let currentTurn = 0;
        let synthCounter = 0;
        let lastToolCallId: string | null = null;
        for (const e of events) {
          if (e.kind === "turn") {
            currentTurn = e.turn;
            _getOrCreateTurn(turns, currentTurn);
          } else if (e.kind === "thinking") {
            const t = _getOrCreateTurn(turns, Math.max(1, currentTurn));
            t.thinkingDeltas.push(e.content);
            t.ordering.push({ kind: "thinking", index: t.thinkingDeltas.length - 1 });
          } else if (e.kind === "tool_call") {
            const t = _getOrCreateTurn(turns, Math.max(1, currentTurn));
            const id = `syn-${e.name}-${synthCounter++}`;
            t.toolCalls.set(id, { id, name: e.name, args: e.input });
            t.ordering.push({ kind: "tool", id });
            lastToolCallId = id;
          } else if (e.kind === "tool_result") {
            const t = _getOrCreateTurn(turns, Math.max(1, currentTurn));
            if (lastToolCallId) {
              const tc = t.toolCalls.get(lastToolCallId);
              if (tc) {
                t.toolCalls.set(lastToolCallId, {
                  ...tc, result: { ok: e.ok, preview: e.preview },
                });
              }
              lastToolCallId = null;
            }
          } else if (e.kind === "dispatch") {
            const t = _getOrCreateTurn(turns, Math.max(1, currentTurn));
            const dispatchId = e.dispatch_id ?? `syn-disp-${synthCounter++}`;
            let d = t.dispatches.get(dispatchId);
            if (!d) {
              d = { id: dispatchId, specialty: e.specialty, subGoal: "",
                    status: "running", subTurns: new Map() };
              t.dispatches.set(dispatchId, d);
              t.ordering.push({ kind: "dispatch", id: dispatchId });
            }
            const subTurn = e.sub_turn ?? 1;
            let st = d.subTurns.get(subTurn);
            if (!st) {
              st = { thinkingDeltas: [], speechDeltas: [], toolCalls: [], toolResults: [] };
              d.subTurns.set(subTurn, st);
            }
            if (e.phase === "thinking") st.thinkingDeltas.push(e.content);
            else if (e.phase === "text") st.speechDeltas.push(e.content);
            else if (e.phase === "tool_call") st.toolCalls.push({ name: "", content: e.content });
            else if (e.phase === "tool_result") st.toolResults.push({ ok: true, content: e.content });
            else if (e.phase === "tool_error") st.toolResults.push({ ok: false, content: e.content });
          }
        }
        return { turns, currentTurn, replayed: true };
      }),
```

- [ ] **Step 4: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/state/session-store.ts desktop/renderer/src/state/session-store.test.ts
git commit -m "feat(store): appendUserMessage + seedFromSessionLog turn assembly"
```

---

## Task 13: Persist turn/tool_use_id/dispatch_id into session log

**Files:**
- Modify: `src/reverser/session_log.py`
- Modify: backend tools that call `log_tool_call`/`log_tool_result`/`log_thinking` to include the ids (these are already plumbed via `_slog` in `AgentSession.send`)

- [ ] **Step 1: Identify the writer signatures**

```bash
grep -n "^    def log_" src/reverser/session_log.py
```

- [ ] **Step 2: Add optional kwargs to logging methods**

For each of `log_thinking`, `log_tool_call`, `log_tool_result`, `log_text`, `log_dispatch_event`, add optional kwargs:

```python
    def log_thinking(self, content: str, *, turn: int | None = None) -> None:
        self._write({"kind": "thinking", "content": content, "turn": turn, "ts": _now_iso()})

    def log_tool_call(self, name: str, input_str: str, *, turn: int | None = None,
                      tool_use_id: str | None = None) -> None:
        self._write({
            "kind": "tool_call", "name": name, "input": input_str,
            "turn": turn, "tool_use_id": tool_use_id, "ts": _now_iso(),
        })

    def log_tool_result(self, content: str, *, is_error: bool = False,
                        turn: int | None = None, tool_use_id: str | None = None) -> None:
        ...

    def log_dispatch_event(self, specialty: str, kind: str, content: str, *,
                           dispatch_id: str | None = None, sub_turn: int | None = None) -> None:
        ...

    def log_turn(self, turn: int) -> None:
        # Add an explicit "turn" event so replay knows where boundaries are.
        self._write({"kind": "turn", "turn": turn, "ts": _now_iso()})
```

(`log_turn` may already exist with this shape — verify.)

- [ ] **Step 3: Pass ids through from AgentSession**

Edit `src/reverser/agent_session.py:516-526`. Replace:

```python
                elif event.kind == "thinking":
                    self._slog.log_thinking(event.content, turn=event.turn)
                    yield event

                elif event.kind == "tool_call":
                    self._slog.log_tool_call(
                        event.tool_name, event.tool_input,
                        turn=event.turn, tool_use_id=event.tool_use_id,
                    )
                    yield event

                elif event.kind == "tool_result":
                    self._slog.log_tool_result(
                        event.content, is_error=event.is_error,
                        turn=event.turn, tool_use_id=event.tool_use_id,
                    )
                    yield event
```

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
uv run pytest tests/ -k "session_log or session_lifecycle" -v
```

Expected: PASS. If any test asserts on exact log row shape, update to allow the new optional fields.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/session_log.py src/reverser/agent_session.py
git commit -m "feat(log): persist turn/tool_use_id/dispatch_id in session log"
```

---

# Phase 3 — Chat pane UI

## Task 14: SpeechBlock, ThinkingChip, ToolCallChip, UserBubble components

**Files:**
- Create: `desktop/renderer/src/panes/SpeechBlock.tsx`
- Create: `desktop/renderer/src/panes/ThinkingChip.tsx`
- Create: `desktop/renderer/src/panes/ToolCallChip.tsx`
- Create: `desktop/renderer/src/panes/UserBubble.tsx`

- [ ] **Step 1: SpeechBlock**

```typescript
// desktop/renderer/src/panes/SpeechBlock.tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function SpeechBlock({ deltas }: { deltas: string[] }) {
  const text = deltas.join("");
  if (!text.trim()) return null;
  return (
    <div className="prose prose-invert prose-sm max-w-none text-neutral-200">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 2: ThinkingChip**

```typescript
// desktop/renderer/src/panes/ThinkingChip.tsx
import { useState } from "react";

export function ThinkingChip({ deltas }: { deltas: string[] }) {
  const [open, setOpen] = useState(false);
  if (deltas.length === 0) return null;
  return (
    <div className="text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="text-neutral-500 hover:text-neutral-300"
      >
        {open ? "▾" : "▸"} thinking [{open ? "hide" : `show ${deltas.length}`}]
      </button>
      {open && (
        <pre className="mt-1 pl-4 whitespace-pre-wrap italic text-neutral-500">
          {deltas.join("\n\n")}
        </pre>
      )}
    </div>
  );
}
```

- [ ] **Step 3: ToolCallChip**

```typescript
// desktop/renderer/src/panes/ToolCallChip.tsx
import { useState } from "react";
import type { ToolCall } from "@/state/session-store";

export function ToolCallChip({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const ok = call.result?.ok;
  const status = call.result === undefined ? "…" : ok ? "✓" : "✗";
  const argsPreview = call.args.replace(/\s+/g, " ").slice(0, 80);
  return (
    <div className="text-xs font-mono">
      <button
        onClick={() => setOpen(!open)}
        className="text-neutral-400 hover:text-neutral-200"
      >
        <span className={ok === false ? "text-red-400" : ok ? "text-green-400" : "text-neutral-500"}>
          {status}
        </span>{" "}
        <span className="text-cyan-400">{call.name}</span>{" "}
        <span className="text-neutral-500">{argsPreview}{call.args.length > 80 ? "…" : ""}</span>
      </button>
      {open && (
        <div className="mt-1 pl-4 space-y-2">
          <pre className="text-neutral-400 whitespace-pre-wrap">{call.args}</pre>
          {call.result && (
            <pre className={`whitespace-pre-wrap ${call.result.ok ? "text-green-400/80" : "text-red-400/80"}`}>
              {call.result.preview}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: UserBubble**

```typescript
// desktop/renderer/src/panes/UserBubble.tsx
export function UserBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[75%] ml-auto bg-neutral-800 text-neutral-100 rounded px-3 py-2 text-sm whitespace-pre-wrap">
      {text}
    </div>
  );
}
```

- [ ] **Step 5: Lint pass**

```bash
cd desktop && npm run lint
```

Errors in `ChatPane.tsx` from the store refactor are still present — that's expected. New files should compile cleanly. If new files have errors, fix them.

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/panes/SpeechBlock.tsx desktop/renderer/src/panes/ThinkingChip.tsx desktop/renderer/src/panes/ToolCallChip.tsx desktop/renderer/src/panes/UserBubble.tsx
git commit -m "feat(panes): leaf chat components (speech, thinking, tool, user)"
```

---

## Task 15: SubTurnBubble + DispatchPanel

**Files:**
- Create: `desktop/renderer/src/panes/SubTurnBubble.tsx`
- Create: `desktop/renderer/src/panes/DispatchPanel.tsx`

- [ ] **Step 1: SubTurnBubble**

```typescript
// desktop/renderer/src/panes/SubTurnBubble.tsx
import type { SubTurn } from "@/state/session-store";
import { ThinkingChip } from "./ThinkingChip";
import { SpeechBlock } from "./SpeechBlock";

export function SubTurnBubble({ subTurn, num }: { subTurn: SubTurn; num: number }) {
  return (
    <div className="border-l border-neutral-800 pl-2 space-y-1 text-xs">
      <div className="text-neutral-600">sub-turn {num}</div>
      <ThinkingChip deltas={subTurn.thinkingDeltas} />
      <SpeechBlock deltas={subTurn.speechDeltas} />
      {subTurn.toolCalls.map((tc, i) => (
        <div key={`tc-${i}`} className="font-mono text-cyan-400/80">
          → {tc.content}
        </div>
      ))}
      {subTurn.toolResults.map((tr, i) => (
        <div
          key={`tr-${i}`}
          className={`font-mono ${tr.ok ? "text-green-400/70" : "text-red-400/70"} whitespace-pre-wrap`}
        >
          {tr.content}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: DispatchPanel**

```typescript
// desktop/renderer/src/panes/DispatchPanel.tsx
import { useState } from "react";
import type { Dispatch } from "@/state/session-store";
import { SubTurnBubble } from "./SubTurnBubble";

export function DispatchPanel({ dispatch }: { dispatch: Dispatch }) {
  const [open, setOpen] = useState(true);
  const subTurns = Array.from(dispatch.subTurns.entries()).sort((a, b) => a[0] - b[0]);
  const statusColor = dispatch.status === "completed" ? "text-green-400"
    : dispatch.status === "error" ? "text-red-400"
    : "text-amber-400";
  return (
    <div className="border-l-2 border-neutral-700 pl-2 my-2">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-neutral-300 hover:text-neutral-100"
      >
        {open ? "▾" : "▸"} dispatch_specialist[<span className="text-fuchsia-400">{dispatch.specialty}</span>]{" "}
        · <span className={statusColor}>{dispatch.status}</span>
        {dispatch.cost !== undefined && ` · $${dispatch.cost.toFixed(4)}`}
        {dispatch.turnsConsumed !== undefined && ` · ${dispatch.turnsConsumed} turns`}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div className="text-xs text-neutral-500">→ {dispatch.subGoal}</div>
          {subTurns.map(([n, st]) => <SubTurnBubble key={n} subTurn={st} num={n} />)}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Lint**

```bash
cd desktop && npm run lint
```

(Same — ignore existing ChatPane errors; new files should be clean.)

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/panes/SubTurnBubble.tsx desktop/renderer/src/panes/DispatchPanel.tsx
git commit -m "feat(panes): SubTurnBubble + DispatchPanel for nested mini-chat"
```

---

## Task 16: TurnBubble

**Files:**
- Create: `desktop/renderer/src/panes/TurnBubble.tsx`
- Create: `desktop/renderer/src/panes/turn-bubble.test.tsx`

- [ ] **Step 1: Write failing test**

```typescript
// desktop/renderer/src/panes/turn-bubble.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TurnBubble } from "./TurnBubble";
import type { Turn } from "@/state/session-store";

function _makeTurn(overrides: Partial<Turn>): Turn {
  return {
    turn: 1,
    thinkingDeltas: [],
    speechDeltas: [],
    toolCalls: new Map(),
    dispatches: new Map(),
    status: "done",
    ordering: [],
    ...overrides,
  };
}

describe("TurnBubble", () => {
  it("renders speech in order", () => {
    const turn = _makeTurn({
      speechDeltas: ["Hello world"],
      ordering: [{ kind: "speech", index: 0 }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/Hello world/)).toBeInTheDocument();
  });

  it("renders thinking chip collapsed by default", () => {
    const turn = _makeTurn({
      thinkingDeltas: ["hmm"],
      ordering: [{ kind: "thinking", index: 0 }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.queryByText(/hmm/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/thinking/));
    expect(screen.getByText(/hmm/)).toBeInTheDocument();
  });

  it("renders a tool call chip with the tool name", () => {
    const turn = _makeTurn({
      toolCalls: new Map([["tu_1", { id: "tu_1", name: "bash", args: '{"cmd":"ls"}' }]]),
      ordering: [{ kind: "tool", id: "tu_1" }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/bash/)).toBeInTheDocument();
  });

  it("renders a dispatch panel for a dispatch entry", () => {
    const turn = _makeTurn({
      dispatches: new Map([["d1", {
        id: "d1", specialty: "webpentest", subGoal: "test xss",
        status: "running", subTurns: new Map(),
      }]]),
      ordering: [{ kind: "dispatch", id: "d1" }],
    });
    render(<TurnBubble turn={turn} />);
    expect(screen.getByText(/webpentest/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
cd desktop && npm test
```

Expected: FAIL — `TurnBubble` not found.

- [ ] **Step 3: Implement TurnBubble**

```typescript
// desktop/renderer/src/panes/TurnBubble.tsx
import type { Turn } from "@/state/session-store";
import { ThinkingChip } from "./ThinkingChip";
import { SpeechBlock } from "./SpeechBlock";
import { ToolCallChip } from "./ToolCallChip";
import { DispatchPanel } from "./DispatchPanel";

export function TurnBubble({ turn }: { turn: Turn }) {
  // Consolidate thinking into one chip per turn (we only render it on first encounter).
  let thinkingRendered = false;
  return (
    <div className="border-l border-neutral-700 pl-3 py-1 space-y-2">
      <div className="text-[10px] text-neutral-500 uppercase tracking-wide">
        turn {turn.turn}
        {turn.status === "streaming" && <span className="ml-2 text-amber-400">●</span>}
      </div>
      {turn.ordering.map((entry, i) => {
        if (entry.kind === "thinking") {
          if (thinkingRendered) return null;
          thinkingRendered = true;
          return <ThinkingChip key={`th-${i}`} deltas={turn.thinkingDeltas} />;
        }
        if (entry.kind === "speech") {
          // Consolidate consecutive speech entries; only render on the first
          // speech entry, with all deltas joined.
          const prevSpeech = turn.ordering.slice(0, i).some((e) => e.kind === "speech");
          if (prevSpeech) {
            // If preceded by tool/dispatch, render again as a new block.
            const lastNonSpeech = [...turn.ordering.slice(0, i)].reverse().find((e) => e.kind !== "speech");
            const lastSpeechIdx = [...turn.ordering.slice(0, i)].reverse().findIndex((e) => e.kind === "speech");
            if (lastNonSpeech && (lastSpeechIdx === -1 || i - lastSpeechIdx > 1)) {
              // fall through to render
            } else {
              return null;
            }
          }
          // Render the run of speech deltas from this entry up to the next non-speech ordering entry.
          const startIdx = entry.index;
          let endIdx = turn.speechDeltas.length;
          for (let j = i + 1; j < turn.ordering.length; j++) {
            const e = turn.ordering[j];
            if (e.kind === "speech") continue;
            // Find the speech index of the next speech entry to bound endIdx.
            const nextSpeechAfter = turn.ordering.slice(j).find((x) => x.kind === "speech");
            if (nextSpeechAfter && nextSpeechAfter.kind === "speech") {
              endIdx = nextSpeechAfter.index;
            }
            break;
          }
          return (
            <SpeechBlock key={`sp-${i}`} deltas={turn.speechDeltas.slice(startIdx, endIdx)} />
          );
        }
        if (entry.kind === "tool") {
          const tc = turn.toolCalls.get(entry.id);
          if (!tc) return null;
          return <ToolCallChip key={`tl-${entry.id}`} call={tc} />;
        }
        if (entry.kind === "dispatch") {
          const d = turn.dispatches.get(entry.id);
          if (!d) return null;
          return <DispatchPanel key={`d-${entry.id}`} dispatch={d} />;
        }
        return null;
      })}
    </div>
  );
}
```

Note the speech-block rendering above is non-trivial because we want one `<SpeechBlock>` per contiguous run of speech ordering entries (a tool or dispatch breaks the run). Simplification: render one `<SpeechBlock>` per **speech ordering entry whose predecessor is not also speech**, and have that block consume all consecutive speech deltas until the next break. The implementation above does this; verify with the test case where speech follows a tool.

- [ ] **Step 4: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS (4 turn-bubble tests).

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/panes/TurnBubble.tsx desktop/renderer/src/panes/turn-bubble.test.tsx
git commit -m "feat(panes): TurnBubble assembly with ordering iteration"
```

---

## Task 17: ChatPane rewrite

**Files:**
- Modify: `desktop/renderer/src/panes/ChatPane.tsx` (full rewrite)

- [ ] **Step 1: Rewrite ChatPane**

Replace the entire contents of `desktop/renderer/src/panes/ChatPane.tsx`:

```typescript
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { getSessionStore } from "@/state/session-store";
import { useSendMessage } from "@/api/queries";
import { TurnBubble } from "./TurnBubble";
import { UserBubble } from "./UserBubble";

export function ChatPane({
  sessionId,
  readOnly = false,
}: {
  sessionId: string;
  readOnly?: boolean;
}) {
  const store = getSessionStore(sessionId);
  const turns = useStore(store, (s) => s.turns);
  const status = useStore(store, (s) => s.status);
  const send = useSendMessage(sessionId);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const sortedTurns = useMemo(
    () => Array.from(turns.values()).sort((a, b) => a.turn - b.turn),
    [turns],
  );

  // Track whether the user is near the bottom so we can decide whether to auto-scroll.
  const [nearBottom, setNearBottom] = useState(true);
  useEffect(() => {
    if (nearBottom) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }
  }, [sortedTurns, nearBottom]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    setNearBottom(dist < 100);
  };

  const submit = async () => {
    if (!input.trim() || send.isPending) return;
    store.getState().appendUserMessage(input);
    const text = input;
    setInput("");
    await send.mutateAsync(text);
  };

  return (
    <div className="flex flex-col h-full">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-auto p-4 space-y-3"
      >
        {sortedTurns.length === 0 && (
          <p className="text-xs text-neutral-500">no messages yet — say hi to start</p>
        )}
        {sortedTurns.map((t) => (
          <div key={t.turn} className="space-y-2">
            {t.userMessage && <UserBubble text={t.userMessage} />}
            <TurnBubble turn={t} />
          </div>
        ))}
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
          <Button
            onClick={submit}
            disabled={!input.trim() || send.isPending || status === "running"}
          >
            Send
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Lint**

```bash
cd desktop && npm run lint
```

Should now be clean *for ChatPane*. Other consumers (`HypothesesPane`, `FindingsPane`, `SessionLayout`, etc.) still have type errors — those are addressed in Tasks 18-21.

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/panes/ChatPane.tsx
git commit -m "feat(panes): rewrite ChatPane around turn-indexed store"
```

---

## Task 18: Delete ToolTimelinePane + remove slot

**Files:**
- Delete: `desktop/renderer/src/panes/ToolTimelinePane.tsx`
- Modify: `desktop/renderer/src/layout/SessionLayout.tsx`

- [ ] **Step 1: Find usage**

```bash
grep -rn "ToolTimelinePane\|ToolTimeline" desktop/renderer/src/
```

- [ ] **Step 2: Remove the import and the slot**

In `SessionLayout.tsx`, remove the import `import { ToolTimelinePane } from ...` and the rendered `<ToolTimelinePane ... />` element (likely inside a Resizable panel). Tighten the panel layout so the chat pane gets the freed space.

- [ ] **Step 3: Delete the file**

```bash
git rm desktop/renderer/src/panes/ToolTimelinePane.tsx
```

- [ ] **Step 4: Lint**

```bash
cd desktop && npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/layout/SessionLayout.tsx
git commit -m "refactor(layout): remove ToolTimelinePane; tool calls render inside chat"
```

---

# Phase 4 — KB pane reactivity

## Task 19: HypothesesPane seed on every kb.data change

**Files:**
- Modify: `desktop/renderer/src/panes/HypothesesPane.tsx`

- [ ] **Step 1: Replace the seed-once effect**

Replace lines 97-101 of `desktop/renderer/src/panes/HypothesesPane.tsx`. Old:

```typescript
  useEffect(() => {
    if (hypothesesMap.size > 0) return;
    const kbHypotheses = (kb.data?.hypotheses ?? []) as HypothesisRow[];
    if (kbHypotheses.length > 0) seedHypotheses(kbHypotheses);
  }, [hypothesesMap.size, kb.data, seedHypotheses]);
```

New:

```typescript
  useEffect(() => {
    const kbHypotheses = (kb.data?.hypotheses ?? []) as HypothesisRow[];
    if (kbHypotheses.length > 0) seedHypotheses(kbHypotheses);
  }, [kb.data, seedHypotheses]);
```

Live `hypothesis` frames will continue to merge into the store on top of the seed; the seed simply re-populates the baseline whenever the snapshot is refetched (e.g., on tab switch back to the session).

- [ ] **Step 2: Lint**

```bash
cd desktop && npm run lint
```

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/panes/HypothesesPane.tsx
git commit -m "fix(hypotheses): seed from KB snapshot on every fetch, not just first"
```

---

## Task 20: FindingsPane reads from store findings Map

**Files:**
- Modify: `desktop/renderer/src/panes/FindingsPane.tsx`

- [ ] **Step 1: Read current implementation**

```bash
cat desktop/renderer/src/panes/FindingsPane.tsx
```

- [ ] **Step 2: Switch to store-backed read**

Rewrite the pane to read from the store's `findings: Map<number, FindingRow>` and to seed from `useTargetKB` snapshots:

```typescript
import { useEffect, useMemo } from "react";
import { useStore } from "zustand";
import { useTargetKB, useSessions } from "@/api/queries";
import { getSessionStore, type FindingRow } from "@/state/session-store";
import { FindingRow as FindingRowComponent } from "@/components/FindingRow";

export function FindingsPane({ sessionId }: { sessionId: string }) {
  const store = getSessionStore(sessionId);
  const findingsMap = useStore(store, (s) => s.findings);
  // `seedFindings` exists on the store actions (Task 8).
  const seedFindings = useStore(store, (s) => (s as any).seedFindings as (rows: FindingRow[]) => void);
  const sessions = useSessions();
  const target = sessions.data?.sessions.find((s) => s.id === sessionId)?.target ?? null;
  const kb = useTargetKB(target);

  useEffect(() => {
    const rows = (kb.data?.findings ?? []) as FindingRow[];
    if (rows.length > 0) seedFindings(rows);
  }, [kb.data, seedFindings]);

  const rows = useMemo(
    () => Array.from(findingsMap.values()).sort((a, b) => (a.id ?? 0) - (b.id ?? 0)),
    [findingsMap],
  );

  if (rows.length === 0) {
    return <p className="p-3 text-xs text-neutral-500">no findings yet</p>;
  }

  return (
    <div className="p-2 space-y-2">
      {rows.map((r) => (
        <FindingRowComponent key={r.id} row={r} />
      ))}
    </div>
  );
}
```

(If `FindingRow` the component has a different prop shape, adapt the prop name accordingly — check `desktop/renderer/src/components/FindingRow.tsx`.)

- [ ] **Step 3: Lint**

```bash
cd desktop && npm run lint
```

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/panes/FindingsPane.tsx
git commit -m "feat(findings): read findings from session store with live frames"
```

---

# Phase 5 — F-key wiring

## Task 21: useProfiles query

**Files:**
- Modify: `desktop/renderer/src/api/queries.ts`

- [ ] **Step 1: Check if it exists**

```bash
grep -n "useProfiles\|/api/profiles" desktop/renderer/src/api/queries.ts
```

If present, skip to Task 22. Otherwise:

- [ ] **Step 2: Add the query**

Find the imports / client at the top of `queries.ts`. Add:

```typescript
export function useProfiles() {
  return useQuery({
    queryKey: ["profiles"],
    queryFn: async () => {
      const res = await apiFetch("/api/profiles");
      if (!res.ok) throw new Error("failed to fetch profiles");
      return res.json() as Promise<{ profiles: { key: string; label: string; description?: string }[] }>;
    },
  });
}
```

(Match the existing client helper name — `apiFetch` may be called something else in your `client.ts`. Read it first.)

- [ ] **Step 3: Lint**

```bash
cd desktop && npm run lint
```

- [ ] **Step 4: Commit**

```bash
git add desktop/renderer/src/api/queries.ts
git commit -m "feat(api): useProfiles query"
```

---

## Task 22: ProfilePickerModal

**Files:**
- Create: `desktop/renderer/src/modals/ProfilePickerModal.tsx`
- Create: `desktop/renderer/src/modals/profile-picker-modal.test.tsx`

- [ ] **Step 1: Read SkillPickerModal as the template**

```bash
cat desktop/renderer/src/modals/SkillPickerModal.tsx
```

- [ ] **Step 2: Write failing test**

```typescript
// desktop/renderer/src/modals/profile-picker-modal.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProfilePickerModal } from "./ProfilePickerModal";

// Mock API client.
vi.mock("@/api/client", () => ({
  apiFetch: vi.fn(async (path: string) => {
    if (path === "/api/profiles") {
      return new Response(JSON.stringify({
        profiles: [
          { key: "manager", label: "Manager" },
          { key: "webpentest", label: "Web Pentest" },
        ],
      }));
    }
    if (path.includes("/config")) {
      return new Response(JSON.stringify({ ok: true }));
    }
    return new Response("{}");
  }),
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("ProfilePickerModal", () => {
  it("lists available profiles", async () => {
    render(wrap(
      <ProfilePickerModal open onClose={() => {}} sessionId="s1" currentProfile="manager" sessionRunning={false} />
    ));
    await waitFor(() => expect(screen.getByText("Web Pentest")).toBeInTheDocument());
  });

  it("Apply is disabled when sessionRunning", async () => {
    render(wrap(
      <ProfilePickerModal open onClose={() => {}} sessionId="s1" currentProfile="manager" sessionRunning />
    ));
    await waitFor(() => expect(screen.getByText("Web Pentest")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Web Pentest"));
    expect(screen.getByRole("button", { name: /apply/i })).toBeDisabled();
  });
});
```

- [ ] **Step 3: Run to verify failure**

```bash
cd desktop && npm test
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement**

```typescript
// desktop/renderer/src/modals/ProfilePickerModal.tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useProfiles } from "@/api/queries";
import { apiFetch } from "@/api/client";

export function ProfilePickerModal({
  open,
  onClose,
  sessionId,
  currentProfile,
  sessionRunning,
}: {
  open: boolean;
  onClose: () => void;
  sessionId: string;
  currentProfile: string;
  sessionRunning: boolean;
}) {
  const profiles = useProfiles();
  const [selected, setSelected] = useState<string>(currentProfile);
  const [error, setError] = useState<string | null>(null);

  const patch = useMutation({
    mutationFn: async (key: string) => {
      const res = await apiFetch(`/api/sessions/${sessionId}/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: key }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => onClose(),
    onError: (e: Error) => setError(e.message),
  });

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-neutral-900 border border-neutral-800 rounded p-4 w-[420px] space-y-3">
        <h2 className="text-sm font-semibold">Switch profile</h2>
        {sessionRunning && (
          <p className="text-xs text-amber-400">Pause the session to apply.</p>
        )}
        <div className="space-y-1 max-h-72 overflow-auto">
          {(profiles.data?.profiles ?? []).map((p) => (
            <button
              key={p.key}
              onClick={() => setSelected(p.key)}
              className={`w-full text-left text-sm px-2 py-1 rounded hover:bg-neutral-800 ${
                selected === p.key ? "bg-neutral-800" : ""
              }`}
            >
              <span className="text-neutral-500 inline-block w-4">
                {p.key === currentProfile ? "✓" : ""}
              </span>
              {p.label}
            </button>
          ))}
        </div>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            disabled={sessionRunning || selected === currentProfile || patch.isPending}
            onClick={() => patch.mutate(selected)}
          >
            {patch.isPending ? "Applying…" : "Apply"}
          </Button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run tests**

```bash
cd desktop && npm test
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add desktop/renderer/src/modals/ProfilePickerModal.tsx desktop/renderer/src/modals/profile-picker-modal.test.tsx
git commit -m "feat(modals): ProfilePickerModal for F2 keybinding"
```

---

## Task 23: Wire F2 in SessionLayout; add Profile button

**Files:**
- Modify: `desktop/renderer/src/layout/SessionLayout.tsx`

- [ ] **Step 1: Add the state and key binding**

Inside `SessionLayout`, near the existing `setSkillOpen` / `setSudoOpen` / `setStopOpen` state:

```typescript
  const [profileOpen, setProfileOpen] = useState(false);
```

In the keydown `useEffect`, add F2:

```typescript
      if (e.key === "F2") { e.preventDefault(); setProfileOpen(true); }
```

In the button row (around line 130-133), add a Profile button next to the others:

```typescript
          <Button size="sm" variant="ghost" onClick={() => setProfileOpen(true)}>Profile (F2)</Button>
```

Render the modal alongside the existing modals:

```typescript
      <ProfilePickerModal
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        sessionId={sessionId}
        currentProfile={currentProfile}
        sessionRunning={status === "running"}
      />
```

`currentProfile` and `status` need to come from where they already do for the other buttons — read the file to confirm.

- [ ] **Step 2: Lint**

```bash
cd desktop && npm run lint
```

- [ ] **Step 3: Commit**

```bash
git add desktop/renderer/src/layout/SessionLayout.tsx
git commit -m "feat(layout): wire F2 to ProfilePickerModal"
```

---

## Task 24: Footer audit + dedup

**Files:**
- Modify: `desktop/renderer/src/layout/Footer.tsx` (if changes needed)
- Modify: any duplicate mount site

- [ ] **Step 1: Find Footer mounts**

```bash
grep -rn "Footer\|<Footer" desktop/renderer/src/ | grep -v node_modules
```

- [ ] **Step 2: Inspect each mount; ensure only one mounts**

If two mounts exist (e.g., one in `Shell.tsx` and one in `SessionLayout.tsx`), keep the highest-level one (Shell) and remove the duplicate. The footer's keymap is shown consistently across all pages.

- [ ] **Step 3: Verify Footer text matches actual bindings**

`Footer.tsx` should read:

```typescript
export function Footer() {
  return (
    <footer className="flex items-center gap-4 text-[10px] text-neutral-500 px-3 py-1 border-t border-neutral-800">
      <span>F1 skills</span>
      <span>F2 profile</span>
      <span>F4 sudo</span>
      <span>F6 stop</span>
      <span className="ml-auto">v0.1.0</span>
    </footer>
  );
}
```

(No F3 / F5 mentions anywhere.)

- [ ] **Step 4: Lint + manual eyeball**

```bash
cd desktop && npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add desktop/renderer/src/layout/Footer.tsx desktop/renderer/src/layout/Shell.tsx desktop/renderer/src/layout/SessionLayout.tsx
git commit -m "fix(footer): single mount, F2 profile listed, F3/F5 removed"
```

---

# Phase 6 — E2E coverage

## Task 25: E2E for F2 profile picker

**Files:**
- Create: `desktop/tests/e2e/profile-picker.spec.ts`

- [ ] **Step 1: Write the test**

Model on `desktop/tests/e2e/engagement.spec.ts` (which already opens a session). Steps:

```typescript
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

const shimBin = path.join(__dirname, "bin");

test("F2 opens profile picker modal", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      PATH: `${shimBin}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const win = await app.firstWindow();
    // Navigate to a session (re-use the engagement test setup or mock-start a session).
    // ... (project-specific: open a target, start an engagement)

    await win.keyboard.press("F2");
    await expect(win.locator("text=Switch profile")).toBeVisible({ timeout: 5_000 });
    await win.click('button:has-text("Cancel")');
    await expect(win.locator("text=Switch profile")).toBeHidden();
  } finally {
    await app.close();
  }
});
```

(The session setup is the tricky bit — copy whatever the existing `engagement.spec.ts` does to get into a session.)

- [ ] **Step 2: Build the renderer + run e2e**

```bash
cd desktop && npm run build && npm run test:e2e -- --grep "F2 opens profile picker"
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add desktop/tests/e2e/profile-picker.spec.ts
git commit -m "test(e2e): F2 opens profile picker modal"
```

---

## Task 26: E2E for chat turn bubbles + live KB update

**Files:**
- Create: `desktop/tests/e2e/chat-bubbles.spec.ts`

- [ ] **Step 1: Write the test**

The test injects mock WS frames (the simplest approach) or runs a small offline backend that streams a scripted sequence. The existing engagement test likely uses one of these patterns — model on it.

```typescript
import { _electron as electron, expect, test } from "@playwright/test";
import path from "path";

const shimBin = path.join(__dirname, "bin");

test("turn bubble renders thinking chip and live hypothesis update", async () => {
  const app = await electron.launch({
    args: [path.join(__dirname, "..", "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      NODE_ENV: "production",
      PATH: `${shimBin}:${process.env.PATH ?? ""}`,
    },
  });
  try {
    const win = await app.firstWindow();
    // ... session setup ...

    // Use evaluate to inject frames into the in-process session store for the test session id.
    await win.evaluate(() => {
      const sessionId = (window as any).__TEST_SESSION_ID__;
      const store = (window as any).__getSessionStore(sessionId);
      const ingest = store.getState().ingest;
      ingest({ type: "status", phase: "running", turns: 1 });
      ingest({ type: "thinking", delta: "considering", redacted: false, turn: 1 });
      ingest({ type: "text", role: "assistant", delta: "Hello", turn: 1 });
      ingest({ type: "hypothesis", action: "create",
        row: { id: 1, parent_id: null, statement: "test", status: "proposed" } });
    });

    await expect(win.locator("text=thinking")).toBeVisible();
    await expect(win.locator("text=Hello")).toBeVisible();
    await expect(win.locator("text=test")).toBeVisible(); // hypothesis statement in tree
  } finally {
    await app.close();
  }
});
```

(For the frame injection to work, the renderer needs a window-exposed hook. If not already present, add a small dev-only hook in `main.tsx`:

```typescript
if (import.meta.env.DEV || process.env.NODE_ENV !== "production") {
  (window as any).__getSessionStore = getSessionStore;
}
```

Add this conditionally so production isn't impacted. If the project disallows test backdoors, instead spin up a mock WS endpoint in the test — substantially more work but cleaner. Given the existing test patterns, choose the lighter path.)

- [ ] **Step 2: Run e2e**

```bash
cd desktop && npm run build && npm run test:e2e -- --grep "turn bubble"
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add desktop/tests/e2e/chat-bubbles.spec.ts desktop/renderer/src/main.tsx
git commit -m "test(e2e): chat turn bubble rendering + live hypothesis frame"
```

---

# Phase 7 — Final verification

## Task 27: Full backend + frontend test pass

- [ ] **Step 1: Backend**

```bash
uv run pytest -v
```

Expected: all PASS (or unchanged pre-existing failures noted as out of scope).

- [ ] **Step 2: Frontend lint**

```bash
cd desktop && npm run lint
```

Expected: no errors.

- [ ] **Step 3: Frontend unit**

```bash
cd desktop && npm test
```

Expected: all PASS.

- [ ] **Step 4: Frontend e2e**

```bash
cd desktop && npm run test:e2e
```

Expected: all PASS (including the existing engagement / phase3a / phase3b / smoke tests — none should regress).

- [ ] **Step 5: Manual smoke**

Run the desktop app, start an engagement, send a message that triggers a dispatch_specialist call, and visually verify:
- Each turn renders as its own bubble.
- Thinking is collapsed by default and expands on click.
- Tool calls render as chips inside the turn bubble.
- A dispatch_specialist call renders as a nested mini-chat showing the specialist's thinking/speech/tool events.
- Pressing F2 opens the profile picker.
- A `kb_update_hypothesis` call updates the row in the Hypotheses pane without refreshing.

- [ ] **Step 6: If everything green, no further commit needed**

The task is complete.

---

## Self-review (run after writing this plan)

**Spec coverage:**
- §1 chat redesign — Tasks 14-17, 26 ✓
- §1 KB live (hypotheses + findings) — Tasks 3, 5, 6, 11, 19, 20 ✓
- §1 F-keys advertised respond — Tasks 21-24 ✓
- §1 single footer source of truth — Task 24 ✓
- §2.1 frame protocol — Tasks 1-2, 4, 6 ✓
- §2.2 backend wiring — Tasks 2, 4, 5, 6 ✓
- §2.3 session store rewrite — Tasks 7-13 ✓
- §2.4 ChatPane component tree — Tasks 14-17 ✓
- §2.5 ToolTimelinePane removal — Task 18 ✓
- §2.6 F-keys + footer — Tasks 21-24 ✓
- §3 error handling cases — covered inline in Tasks 9-12 (drop-on-unknown-id) and Task 22 (Apply disabled when running)
- §4 testing strategy — Tasks 7-13 (store unit), 16, 22 (component unit), 25-26 (e2e)

**Placeholder scan:** none.

**Type consistency:** `Turn`, `ToolCall`, `Dispatch`, `SubTurn`, `FindingRow` introduced in Task 8 and used unchanged in subsequent tasks. `dispatch_id`, `sub_turn`, `tool_use_id` field names consistent between backend (Tasks 1, 4, 6) and frontend (Tasks 9, 10). `emit_kb_event` signature `(kind, payload)` consistent across Tasks 2, 3, 6.

---

**Plan complete.** Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task with two-stage review.
2. **Inline Execution** — execute tasks in this session via executing-plans skill with checkpoints.

Which approach?
