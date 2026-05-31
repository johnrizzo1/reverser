# API Error Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LLM API errors recoverable — the OpenAI-compat backend retries transient failures (429/503/conn/timeout) with exponential backoff without re-running tools, and the Claude backend handles the SDK's rate-limit event + wraps `query()` so failures surface as clean, classified, resumable error events instead of raw crashes.

**Architecture:** A shared `backends/retry.py` (`classify_error` + `call_with_retries`, injectable sleep/jitter) used by the OpenAI-compat backend to retry just the per-turn `chat.completions.create()` call. The Claude backend (whose loop is owned by `claude_agent_sdk.query()`) is NOT retried at our layer; instead it handles the yielded `RateLimitEvent` and wraps the iteration in try/except. Errors surface as `AgentEvent(kind="error", subtype=...)` — no agent-loop/consumer changes.

**Tech Stack:** Python 3.11+, `openai` (already a dep), `claude-agent-sdk`, asyncio, pytest/pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-05-31-api-error-resilience-design.md](../specs/2026-05-31-api-error-resilience-design.md)

**Test command:** `PYTHONPATH="$PWD/src" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.venv/bin/python -m pytest <args>` (in the worktree).

---

## File Structure

- **Create** `src/reverser/backends/retry.py` — `classify_error(exc) -> str`, `call_with_retries(...)`.
- **Modify** `src/reverser/backends/openai_compat.py` — wrap the per-turn `create()` in `call_with_retries`; emit `llm_status` "retrying" events; classify exhausted/terminal errors into `subtype`.
- **Modify** `src/reverser/backends/claude.py` — handle the yielded rate-limit event (via a `getattr` chain) + wrap the `query()` iteration in try/except.
- **Tests** `tests/test_backend_retry.py`, `tests/test_openai_backend_retry.py`, `tests/test_claude_backend_resilience.py`.

**Verified facts (from codebase inspection):**
- `AgentEvent` (base.py) kinds include `error`, `result`, `llm_status`; fields include `content`, `subtype`, `is_error`. An `error` event + a `result` event with `subtype="error"` is the existing "turn failed" pattern.
- `openai` exposes `RateLimitError` (429, `status_code=429`), `APIStatusError` (`status_code`), `APIConnectionError`, `APITimeoutError`. `RateLimitError`/`APITimeoutError` are subclasses, so classify by `status_code`/class-name (duck-typed) — not brittle isinstance ordering.
- `claude_agent_sdk` exports `RateLimitEvent` with `.rate_limit_info.status` ∈ {allowed, allowed_warning, rejected}. We detect via `getattr(getattr(msg,"rate_limit_info",None),"status",None)` so tests need no constructor.
- `openai_compat.py` calls `await self._client.chat.completions.create(model=..., messages=messages, tools=..., extra_body=..., stream=True)` inside `while turn < max_turns:` and currently has a `try/except Exception` that special-cases the `n_keep`/`n_ctx` context-window error. Tools run AFTER the model responds → retrying `create()` re-runs nothing.

---

## Task 1: shared retry core (`retry.py`)

**Files:**
- Create: `src/reverser/backends/retry.py`
- Test: `tests/test_backend_retry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backend_retry.py`:

```python
import pytest

from reverser.backends.retry import classify_error, call_with_retries


# ── classify_error (duck-typed; fakes mirror the openai surface) ──

class _FakeRateLimit(Exception):
    status_code = 429

class _FakeAuth(Exception):
    status_code = 401

class _FakeServer(Exception):
    status_code = 503

class APIConnectionError(Exception):  # name match, no status_code
    pass

class APITimeoutError(Exception):
    pass

class _Weird(Exception):
    pass


@pytest.mark.parametrize("exc,expected", [
    (_FakeRateLimit(), "transient"),
    (_FakeServer(), "transient"),
    (APIConnectionError(), "transient"),
    (APITimeoutError(), "transient"),
    (_FakeAuth(), "terminal"),
    (_Weird(), "terminal"),          # unknown → terminal (fail safe)
])
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


def test_classify_408_409_transient():
    class E408(Exception):
        status_code = 408
    class E409(Exception):
        status_code = 409
    assert classify_error(E408()) == "transient"
    assert classify_error(E409()) == "transient"


# ── call_with_retries ──

@pytest.mark.asyncio
async def test_returns_on_first_success():
    sleeps = []
    async def call():
        return "ok"
    out = await call_with_retries(call, sleep=lambda d: _noop(sleeps, d))
    assert out == "ok" and sleeps == []


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds():
    sleeps, retries = [], []
    calls = {"n": 0}
    async def call():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeRateLimit()
        return "ok"
    out = await call_with_retries(
        call, sleep=lambda d: _noop(sleeps, d),
        on_retry=lambda a, d, e: retries.append((a, d)),
        rng=lambda: 0.0,
    )
    assert out == "ok"
    assert len(sleeps) == 1 and len(retries) == 1 and retries[0][0] == 1
    assert sleeps[0] == 2.0  # base_delay * 2**0, no jitter


@pytest.mark.asyncio
async def test_terminal_reraises_immediately():
    sleeps = []
    async def call():
        raise _FakeAuth()
    with pytest.raises(_FakeAuth):
        await call_with_retries(call, sleep=lambda d: _noop(sleeps, d))
    assert sleeps == []  # never slept


@pytest.mark.asyncio
async def test_exhausts_then_reraises():
    sleeps = []
    async def call():
        raise _FakeRateLimit()
    with pytest.raises(_FakeRateLimit):
        await call_with_retries(call, max_retries=3, sleep=lambda d: _noop(sleeps, d), rng=lambda: 0.0)
    assert sleeps == [2.0, 4.0, 8.0]  # 3 retries, exponential, no jitter


@pytest.mark.asyncio
async def test_backoff_caps():
    sleeps = []
    async def call():
        raise _FakeRateLimit()
    with pytest.raises(_FakeRateLimit):
        await call_with_retries(call, max_retries=6, base_delay=2.0, cap=30.0,
                                sleep=lambda d: _noop(sleeps, d), rng=lambda: 0.0)
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]  # capped at 30


async def _noop(store, d):
    store.append(d)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_backend_retry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.backends.retry'`.

- [ ] **Step 3: Implement**

Create `src/reverser/backends/retry.py`:

```python
"""Shared transient-error retry/backoff for LLM backends.

classify_error is duck-typed (status_code / class name) so it works for the
openai exception hierarchy without brittle isinstance ordering, and stays unit-
testable with lightweight fakes.
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

# Class names that are transient even without a status_code (network layer).
_TRANSIENT_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "APIConnectionTimeoutError",
}


def classify_error(exc: BaseException) -> str:
    """Return 'transient' (worth retrying) or 'terminal' (do not retry).

    Unknown errors are 'terminal' on purpose — never loop on something we don't
    understand.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is not None:
        try:
            code = int(code)
        except (TypeError, ValueError):
            return "terminal"
        if code >= 500 or code in (408, 409, 429):
            return "transient"
        return "terminal"
    if type(exc).__name__ in _TRANSIENT_NAMES:
        return "transient"
    return "terminal"


async def call_with_retries(
    make_call: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 2.0,
    cap: float = 30.0,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    rng: Callable[[], float] = random.random,
) -> T:
    """Await make_call(); retry transient failures with capped exponential
    backoff. Terminal errors re-raise immediately; exhaustion re-raises the last
    error. `on_retry(attempt, delay, exc)` fires before each backoff sleep.
    `sleep`/`rng` are injectable for deterministic tests; when `sleep` is None we
    resolve `asyncio.sleep` at call time so tests can monkeypatch it on the module."""
    _sleep = sleep if sleep is not None else asyncio.sleep
    attempt = 0
    while True:
        try:
            return await make_call()
        except Exception as exc:  # noqa: BLE001 — classify decides
            if classify_error(exc) == "terminal" or attempt >= max_retries:
                raise
            delay = min(cap, base_delay * (2 ** attempt))
            delay += rng() * min(1.0, delay * 0.1)  # jitter: up to 10%, capped at 1s
            if on_retry is not None:
                on_retry(attempt + 1, delay, exc)
            await _sleep(delay)
            attempt += 1
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_backend_retry.py -v`
Expected: PASS (all). Note jitter is 0 in tests via `rng=lambda: 0.0`, so the `sleeps` lists are exact.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/backends/retry.py tests/test_backend_retry.py
git commit -m "feat(backends): shared classify_error + call_with_retries (backoff helper)"
```

---

## Task 2: OpenAI-compat backend retry wiring

**Files:**
- Modify: `src/reverser/backends/openai_compat.py`
- Test: `tests/test_openai_backend_retry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_openai_backend_retry.py`:

```python
import pytest
from types import SimpleNamespace

from reverser.backends.openai_compat import OpenAICompatBackend


class _RateLimit(Exception):
    status_code = 429

class _Auth(Exception):
    status_code = 401


def _make_backend(create_fn):
    be = OpenAICompatBackend(tools=[], model="m", api_base="http://x/v1")
    # replace the real client with a fake exposing chat.completions.create
    be._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn))
    )
    return be


async def _collect(be, *, sleep):
    # patch the module's asyncio.sleep so retries don't actually wait
    import reverser.backends.retry as r
    events = []
    async for ev in be.run(prompt="hi", system_prompt="sys", max_turns=1):
        events.append(ev)
    return events


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def _nosleep(_):
        return None
    import reverser.backends.retry as r
    monkeypatch.setattr(r.asyncio, "sleep", _nosleep)


@pytest.mark.asyncio
async def test_persistent_rate_limit_emits_rate_limited(monkeypatch):
    async def create(**kw):
        raise _RateLimit()
    be = _make_backend(create)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "rate_limited"
    # retried 3 times -> 3 llm_status "retrying" notices
    retry_notes = [e for e in events if e.kind == "llm_status" and "retry" in e.content.lower()]
    assert len(retry_notes) == 3


@pytest.mark.asyncio
async def test_auth_error_is_terminal_no_retry(monkeypatch):
    calls = {"n": 0}
    async def create(**kw):
        calls["n"] += 1
        raise _Auth()
    be = _make_backend(create)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    assert calls["n"] == 1  # zero retries
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "quota_exhausted"
    assert not any(e.kind == "llm_status" and "retry" in e.content.lower() for e in events)


@pytest.mark.asyncio
async def test_context_window_error_terminal_with_help(monkeypatch):
    calls = {"n": 0}
    async def create(**kw):
        calls["n"] += 1
        raise Exception("n_keep n_ctx mismatch")
    be = _make_backend(create)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    assert calls["n"] == 1  # not retried
    errs = [e for e in events if e.kind == "error"]
    assert errs and "Context Length" in errs[-1].content
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_openai_backend_retry.py -v`
Expected: FAIL — current code retries nothing (`calls["n"]` for rate-limit is 1, no `rate_limited` subtype, no retry notices).

- [ ] **Step 3: Implement**

In `openai_compat.py`, add the import near the top imports:

```python
from .retry import call_with_retries, classify_error
```

Replace the existing `try: response = await self._client.chat.completions.create(...) except Exception as e: ...` block with a retried call. The context-window message is computed by a helper and the special-case stays terminal. New code (preserving the surrounding `yield AgentEvent(kind="turn"...)` / status events):

```python
            _retry_notes: list[str] = []

            def _on_retry(attempt, delay, exc):
                _retry_notes.append(
                    f"LLM API error ({type(exc).__name__}); retrying in "
                    f"{delay:.0f}s (attempt {attempt}/3)"
                )

            async def _make_call():
                return await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools_for_model if tools_for_model else None,
                    extra_body={"think": True},
                    stream=True,
                )

            try:
                response = await call_with_retries(_make_call, on_retry=_on_retry)
            except Exception as e:
                for note in _retry_notes:
                    yield AgentEvent(kind="llm_status", content=note)
                err = str(e)
                if "n_keep" in err and "n_ctx" in err:
                    err = (
                        f"{err}\n\n"
                        "The model's context window is too small for the "
                        "agent prompt + tools. In LM Studio, select the model "
                        "and increase 'Context Length' to at least 16384 "
                        "(32768 recommended), then reload the model."
                    )
                code = getattr(e, "status_code", None) or getattr(
                    getattr(e, "response", None), "status_code", None)
                if code in (429,):
                    subtype = "rate_limited"
                elif code in (401, 403):
                    subtype = "quota_exhausted"
                else:
                    subtype = "api_error"
                yield AgentEvent(kind="error", content=err, subtype=subtype, is_error=True)
                yield AgentEvent(kind="result", content=f"Error: {err}", subtype="error")
                return
            else:
                for note in _retry_notes:
                    yield AgentEvent(kind="llm_status", content=note)
```

Notes:
- `_on_retry` only appends to `_retry_notes` (a callback can't `yield`); the notes are drained into `llm_status` events on BOTH paths (success `else:` and failure) right after `call_with_retries` returns/raises, so the user sees "retrying…" regardless of outcome.
- The context-window error has no `status_code`, so `classify_error` returns "terminal" → it is NOT retried (the test asserts `calls["n"] == 1`). Its subtype falls through to `api_error`, and the helpful message is preserved.
- Everything after `response = ...` (the streaming/parsing block) is unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_openai_backend_retry.py -v`
Then regression: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_openai_compat_deepseek.py tests/test_openai_backend_ids.py tests/test_backend_factory.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/backends/openai_compat.py tests/test_openai_backend_retry.py
git commit -m "feat(backends): retry transient OpenAI-compat API errors with backoff"
```

---

## Task 3: Claude backend resilience

**Files:**
- Modify: `src/reverser/backends/claude.py`
- Test: `tests/test_claude_backend_resilience.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_backend_resilience.py`:

```python
import pytest
from types import SimpleNamespace

import reverser.backends.claude as claude_mod
from reverser.backends.claude import ClaudeBackend


@pytest.fixture(autouse=True)
def _stub_sdk(monkeypatch):
    # create_sdk_mcp_server must not do real work
    monkeypatch.setattr(claude_mod, "create_sdk_mcp_server", lambda **kw: object())


def _fake_query(messages):
    async def _q(*, prompt, options):
        for m in messages:
            yield m
    return _q


@pytest.mark.asyncio
async def test_rate_limit_rejected_emits_quota_exhausted(monkeypatch):
    rl = SimpleNamespace(rate_limit_info=SimpleNamespace(status="rejected"))
    monkeypatch.setattr(claude_mod, "query", _fake_query([rl]))
    be = ClaudeBackend(tools=[])
    events = [e async for e in be.run(prompt="hi", system_prompt="s")]
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "quota_exhausted"


@pytest.mark.asyncio
async def test_query_exception_surfaces_as_api_error(monkeypatch):
    async def _boom(*, prompt, options):
        raise RuntimeError("subprocess died")
        yield  # pragma: no cover (make it an async generator)
    monkeypatch.setattr(claude_mod, "query", _boom)
    be = ClaudeBackend(tools=[])
    # must NOT raise — surfaces a clean error event
    events = [e async for e in be.run(prompt="hi", system_prompt="s")]
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "api_error"
    assert "subprocess died" in errs[-1].content


@pytest.mark.asyncio
async def test_rate_limit_warning_is_non_fatal(monkeypatch):
    warn = SimpleNamespace(rate_limit_info=SimpleNamespace(status="allowed_warning"))
    result = SimpleNamespace(subtype="success", result="done", total_cost_usd=0.0, num_turns=1)
    # ResultMessage is matched by isinstance in run(); use the real class
    from claude_agent_sdk import ResultMessage
    rmsg = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                         is_error=False, num_turns=1, session_id="s", total_cost_usd=0.0,
                         usage={}, result="done")
    monkeypatch.setattr(claude_mod, "query", _fake_query([warn, rmsg]))
    be = ClaudeBackend(tools=[])
    events = [e async for e in be.run(prompt="hi", system_prompt="s")]
    assert any(e.kind == "llm_status" for e in events)  # heads-up emitted
    assert not any(e.kind == "error" for e in events)   # warning is not an error
```

(If `ResultMessage`'s constructor args differ, build it however the SDK requires — inspect
`from claude_agent_sdk import ResultMessage; help(ResultMessage)`; the only thing the test needs is
that `run()` reaches a normal `result` event with no `error`. If constructing a real `ResultMessage`
is awkward, drop the `rmsg` and assert just the `llm_status` + no-error on a `[warn]`-only stream.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_claude_backend_resilience.py -v`
Expected: FAIL — rate-limit event ignored (no `quota_exhausted`); `query()` raising propagates (test errors instead of getting an `api_error` event).

- [ ] **Step 3: Implement**

In `claude.py`, add a small helper at module level (after imports):

```python
def _rate_limit_status(message) -> str | None:
    """Return the rate-limit status ('allowed'|'allowed_warning'|'rejected') if
    this is a rate-limit event, else None. Duck-typed so it works for the SDK's
    RateLimitEvent without depending on its constructor."""
    return getattr(getattr(message, "rate_limit_info", None), "status", None)
```

Then wrap the iteration in `run()` and handle the rate-limit event at the top of the loop. Replace:

```python
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                ...
```

with:

```python
        try:
            async for message in query(prompt=prompt, options=options):
                rl_status = _rate_limit_status(message)
                if rl_status is not None:
                    if rl_status == "rejected":
                        yield AgentEvent(
                            kind="error",
                            content="Claude API rate limit/quota rejected the request; "
                                    "cannot continue this turn.",
                            subtype="quota_exhausted",
                            is_error=True,
                        )
                        yield AgentEvent(kind="result", content="Error: quota_exhausted",
                                         subtype="error")
                        return
                    if rl_status == "allowed_warning":
                        yield AgentEvent(kind="llm_status",
                                         content="Approaching Claude API rate limit.")
                    continue
                if isinstance(message, AssistantMessage):
                    ...  # (unchanged body)
                elif isinstance(message, UserMessage):
                    ...  # (unchanged)
                elif isinstance(message, ResultMessage):
                    ...  # (unchanged)
        except Exception as e:
            yield AgentEvent(kind="error", content=f"Claude backend error: {e}",
                             subtype="api_error", is_error=True)
            yield AgentEvent(kind="result", content=f"Error: {e}", subtype="error")
            return
```

Keep the existing `AssistantMessage`/`UserMessage`/`ResultMessage` handling bodies exactly as they
are — only add the rate-limit check at the top of the loop and the surrounding `try/except`. (Indent
the existing loop body by one level under the new `try`.)

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest tests/test_claude_backend_resilience.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/backends/claude.py tests/test_claude_backend_resilience.py
git commit -m "feat(backends): Claude handles rate-limit event + wraps query() failures"
```

---

## Task 4: Full regression

**Files:** none — verification only.

- [ ] **Step 1: Full suite**

Run: `PYTHONPATH="$PWD/src" .venv-python -m pytest -q`
Expected: all green (≤1 skipped). Investigate any failure (most likely a backend test that monkeypatches `create`/`query` differently — reconcile).

- [ ] **Step 2: Backends import + factory build**

Run: `PYTHONPATH="$PWD/src" .venv-python -c "from reverser.backends import create_backend; from reverser.backends.retry import classify_error, call_with_retries; print('ok')"`
Expected: prints `ok`, no import error.

- [ ] **Step 3: Commit (if any cleanup)**

```bash
git add -A
git commit -m "test: full regression green for API error resilience"
```

---

## Self-Review notes

- **Spec coverage:** shared `retry.py` classify+backoff (Task 1); OpenAI-compat per-request retry +
  `llm_status` retry events + classified subtypes + preserved context-window terminal (Task 2);
  Claude `RateLimitEvent` handling (rejected→quota_exhausted, warning→llm_status) + `query()`
  try/except→api_error (Task 3); surfacing via existing consumers (no change needed); full regression
  (Task 4). All spec sections map to a task.
- **Confirm-during-TDD:** the real `ResultMessage` constructor args for the Claude warning test (Task
  3 Step 1 has a documented fallback if awkward — use a `[warn]`-only stream and assert `llm_status` +
  no-error). The `sleep=None`→resolve-`asyncio.sleep`-at-call-time indirection is baked into Task 1 so
  the Task 2 autouse fixture's `monkeypatch.setattr(r.asyncio, "sleep", ...)` actually speeds up retries.
- **Type consistency:** `classify_error -> "transient"|"terminal"`; `call_with_retries(make_call, *,
  max_retries, base_delay, cap, on_retry, sleep, rng)`; error subtypes `rate_limited|quota_exhausted|
  api_error` used consistently across Tasks 2–3 and the spec.
```
