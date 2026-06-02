# Local-Backend Cost / Token Tracking (Gap 5-1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The OpenAI-compat backend tracks tokens per turn (API `usage` when present, char/4 estimate otherwise), always surfaces a token count on its result events, and converts tokens → a pseudo-USD `cost` via an opt-in `token_cost_per_1k` rate — reusing the existing dollar plumbing so the future budget gate works for local backends.

**Architecture:** A tiny pure token helper; per-turn token accounting accumulated across the run inside `OpenAICompatBackend.run`; a `token_cost_per_1k` config threaded `SessionConfig → create_backend → backend`; `cost`/`tokens` populated on every result event. Default rate 0.0 = no dollar change (tokens still tracked). Claude backend untouched.

**Tech Stack:** Python 3.11+, `openai` AsyncOpenAI streaming, pytest/pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-05-31-local-backend-cost-tracking-design.md](../specs/2026-05-31-local-backend-cost-tracking-design.md)

**Test command:** `PYTHONPATH="$PWD/src" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.venv/bin/python -m pytest <args>`

---

## File Structure

- **Create** `src/reverser/backends/tokens.py` — `estimate_tokens(text)` + `tokens_from_usage(usage_obj)` pure helpers.
- **Modify** `src/reverser/backends/base.py` — `AgentEvent.tokens: int | None = None`.
- **Modify** `src/reverser/backends/openai_compat.py` — `__init__(token_cost_per_1k=0.0)`; per-turn token accounting; `cost`/`tokens` on the 3 result emits; `stream_options` capability probe.
- **Modify** `src/reverser/backends/__init__.py` — `create_backend(..., token_cost_per_1k=0.0)` forwarded to OpenAICompatBackend.
- **Modify** `src/reverser/sessions.py` — `SessionConfig.token_cost_per_1k: float = 0.0`.
- **Modify** `src/reverser/cli.py` (+ session-construction seam) — `--token-cost-per-1k` flag → SessionConfig → create_backend.
- **Modify** `src/reverser/tools/dispatch.py` — forward `cfg.token_cost_per_1k` into the dispatched sub-agent's `create_backend`.
- **Tests** — `tests/test_token_accounting.py` (helpers), `tests/test_openai_cost_tracking.py` (backend), extend `tests/test_session_resume.py` + `tests/test_backend_factory.py` + a CLI test.

**Verified facts (read on current `main`):**
- `OpenAICompatBackend.__init__(self, tools, model, api_base="http://localhost:11434/v1", api_key="not-needed", model_family=None)`; sets `self._model`, `self._family`, etc.
- `run()` loops `while turn < max_turns:`; the request is `_make_call()` → `await self._client.chat.completions.create(model=, messages=, tools=, extra_body={"think": True}, stream=True)`, wrapped in `call_with_retries(_make_call, on_retry=_on_retry)` (Gap-4). On exception → error+result emit (~465). The stream loop is `async for chunk in response:` with `choices = _obj_get(chunk, "choices", []) or []; if not choices: continue` (~483-485). It accumulates `generated_chars` (content + reasoning deltas). Result emits: error `subtype="error"` (~465), `subtype="success"` (~717-722), `subtype="max_turns"` (~770-775).
- `_obj_get(obj, key, default)` is a helper in the module that reads attr-or-dict (works for both pydantic objects and dicts).
- `create_backend(name, tools, *, model=None, api_base=None, model_family=None)`; for non-claude it does `return OpenAICompatBackend(tools, model=model, api_base=api_base, model_family=model_family)`.
- `SessionConfig` fields: `profile, backend, model, api_base, validation_backend, validation_model, validation_api_base, budget, max_turns, max_parallel`; serialized via `asdict`, loaded `SessionConfig(**data)`.
- `AgentEvent` (base.py) has `cost: float | None = None`; tokens field does NOT exist yet.

---

## Task 1: token helpers (`tokens.py`)

**Files:** Create `src/reverser/backends/tokens.py`; create `tests/test_token_accounting.py`.

- [ ] **Step 1: Write the failing test** — create `tests/test_token_accounting.py`:

```python
from types import SimpleNamespace

from reverser.backends.tokens import estimate_tokens, tokens_from_usage


def test_estimate_tokens_char_over_4():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1       # 4 chars -> 1
    assert estimate_tokens("abcde") == 2      # ceil(5/4) = 2
    assert estimate_tokens("a" * 400) == 100


def test_estimate_tokens_none_safe():
    assert estimate_tokens(None) == 0


def test_tokens_from_usage_object():
    u = SimpleNamespace(prompt_tokens=30, completion_tokens=12)
    assert tokens_from_usage(u) == 42


def test_tokens_from_usage_dict():
    assert tokens_from_usage({"prompt_tokens": 5, "completion_tokens": 7}) == 12


def test_tokens_from_usage_missing_or_none():
    assert tokens_from_usage(None) == 0
    assert tokens_from_usage(SimpleNamespace(prompt_tokens=None, completion_tokens=None)) == 0
    assert tokens_from_usage({"prompt_tokens": 9}) == 9  # missing completion -> 0
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_token_accounting.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — create `src/reverser/backends/tokens.py`:

```python
"""Token-count helpers for backends that don't report cost natively.

estimate_tokens: rough char/4 heuristic (the standard ballpark for English text).
tokens_from_usage: read prompt+completion tokens from an OpenAI-style usage block
(object or dict), tolerating missing/None fields.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str | None) -> int:
    """Rough token estimate: ceil(len/4). None/empty -> 0."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def _num(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def tokens_from_usage(usage) -> int:
    """Total tokens (prompt + completion) from an OpenAI usage block (object or
    dict). Missing/None/unparseable fields count as 0; returns 0 if usage is None."""
    if usage is None:
        return 0
    if isinstance(usage, dict):
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    else:
        pt, ct = getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
    return _num(pt) + _num(ct)
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_token_accounting.py -v` → PASS (5).

- [ ] **Step 5: Commit**
```bash
git add src/reverser/backends/tokens.py tests/test_token_accounting.py
git commit -m "feat(backends): token estimate + usage-parse helpers"
```

---

## Task 2: AgentEvent.tokens field

**Files:** Modify `src/reverser/backends/base.py`; extend `tests/test_token_accounting.py` (or a 1-liner anywhere).

- [ ] **Step 1: Write the failing test** — append to `tests/test_token_accounting.py`:

```python
def test_agent_event_has_tokens_field():
    from reverser.backends.base import AgentEvent
    e = AgentEvent(kind="result", cost=0.5, tokens=1234)
    assert e.tokens == 1234
    assert AgentEvent(kind="text").tokens is None
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_token_accounting.py -k tokens_field -v` → `TypeError: ... unexpected keyword argument 'tokens'`.

- [ ] **Step 3: Implement** — in `base.py`, add to the `AgentEvent` dataclass next to `cost`:
```python
    tokens: int | None = None        # total tokens for this run (local backends)
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_token_accounting.py -k tokens_field -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/reverser/backends/base.py tests/test_token_accounting.py
git commit -m "feat(backends): AgentEvent.tokens optional field"
```

---

## Task 3: OpenAI-compat token accounting + cost on result events

**Files:** Modify `src/reverser/backends/openai_compat.py`; create `tests/test_openai_cost_tracking.py`.

- [ ] **Step 1: Write the failing test** — create `tests/test_openai_cost_tracking.py`:

```python
import pytest
from types import SimpleNamespace

from reverser.backends.openai_compat import OpenAICompatBackend


def _chunk(content=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, reasoning=None, reasoning_content=None, tool_calls=None)
    choices = [] if content is None and finish is None else [
        SimpleNamespace(delta=delta, finish_reason=finish, index=0)]
    return SimpleNamespace(choices=choices, usage=usage)


def _make_stream(chunks):
    async def _agen():
        for c in chunks:
            yield c
    return _agen()


def _backend(create_fn, *, token_cost_per_1k=0.0):
    be = OpenAICompatBackend(tools=[], model="m", api_base="http://x/v1",
                             token_cost_per_1k=token_cost_per_1k)
    be._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))
    return be


@pytest.mark.asyncio
async def test_tokens_from_usage_reported(monkeypatch):
    # one substantive text turn then done; final usage chunk
    async def create(**kw):
        return _make_stream([
            _chunk(content="this is a sufficiently long answer to be treated as done " * 3,
                   finish="stop"),
            _chunk(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40)),
        ])
    be = _backend(create, token_cost_per_1k=2.0)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    result = [e for e in events if e.kind == "result"][-1]
    assert result.tokens == 140
    assert result.cost == pytest.approx(140 / 1000 * 2.0)


@pytest.mark.asyncio
async def test_tokens_estimated_when_no_usage(monkeypatch):
    text = "x" * 400  # ~100 tokens of generated content
    async def create(**kw):
        return _make_stream([_chunk(content=text, finish="stop")])
    be = _backend(create, token_cost_per_1k=0.0)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    result = [e for e in events if e.kind == "result"][-1]
    # estimate = prompt_chars/4 + generated_chars/4 ; generated ~100 tokens, prompt > 0
    assert result.tokens >= 100
    assert result.cost == 0.0   # rate 0 -> no dollars, but tokens tracked


@pytest.mark.asyncio
async def test_cost_zero_rate_tokens_still_tracked(monkeypatch):
    async def create(**kw):
        return _make_stream([
            _chunk(content="long enough answer " * 5, finish="stop"),
            _chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)),
        ])
    be = _backend(create, token_cost_per_1k=0.0)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    result = [e for e in events if e.kind == "result"][-1]
    assert result.tokens == 15 and result.cost == 0.0
```

(NOTE: the backend may inject "continue"/"do something" follow-up turns if the visible text is short or no tools were used — that's why the test content is long. If a test still loops past `max_turns=1`, the result will be the `max_turns` event; that's fine as long as `tokens`/`cost` are populated there too. Read the run() control flow and adjust the content length / max_turns so the assertion targets a result event that carries the accumulated tokens.)

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_openai_cost_tracking.py -v` → FAIL (`token_cost_per_1k` not accepted; result events have no tokens/cost).

- [ ] **Step 3: Implement** — in `openai_compat.py`:

(a) `__init__`: add `token_cost_per_1k: float = 0.0` after `model_family`; store `self._token_cost_per_1k = token_cost_per_1k`.

(b) Token import at top: `from .tokens import estimate_tokens, tokens_from_usage`.

(c) In `run()`, BEFORE the `while turn < max_turns:` loop, initialize run accumulators:
```python
        run_tokens = 0
```
And add a helper closure (after the accumulators) to compute the cost from tokens:
```python
        def _cost(tok: int) -> float:
            return tok / 1000.0 * self._token_cost_per_1k
```

(d) Request `usage` in the stream. Change `_make_call` to include `stream_options`, with a one-time capability fallback. Replace the `_make_call`/`call_with_retries` block so it tries with `stream_options` and, on a 400 mentioning `stream_options`, retries once without it for the rest of the run:
```python
            async def _make_call():
                kwargs = dict(
                    model=self._model, messages=messages,
                    tools=tools_for_model if tools_for_model else None,
                    extra_body={"think": True}, stream=True,
                )
                if self._stream_usage_supported:
                    kwargs["stream_options"] = {"include_usage": True}
                try:
                    return await self._client.chat.completions.create(**kwargs)
                except Exception as e:  # noqa: BLE001
                    if self._stream_usage_supported and "stream_options" in str(e).lower():
                        # server rejects stream_options; disable for the rest of the run
                        self._stream_usage_supported = False
                        kwargs.pop("stream_options", None)
                        return await self._client.chat.completions.create(**kwargs)
                    raise
```
and initialize `self._stream_usage_supported = True` in `__init__` (instance flag so the probe only happens once per backend). The `call_with_retries(_make_call, ...)` wrapper is unchanged — the probe lives inside `_make_call`, so the Gap-4 retry classification still applies to genuine transient errors.

(e) Capture usage in the chunk loop. The usage chunk has empty `choices`, so capture it BEFORE the `if not choices: continue`. Add a per-turn `turn_usage_tokens = 0` just before `async for chunk in response:`, and right after `choices = _obj_get(chunk, "choices", []) or []` add:
```python
                    _usage = _obj_get(chunk, "usage", None)
                    if _usage is not None:
                        turn_usage_tokens = tokens_from_usage(_usage) or turn_usage_tokens
```
(keep the existing `if not choices: continue` immediately after — usage chunks fall through to continue, which is correct.)

(f) After the stream loop completes for the turn (where `generated_chars` is final, before the result/tool-handling branches), compute the per-turn tokens and accumulate:
```python
                if turn_usage_tokens > 0:
                    run_tokens += turn_usage_tokens
                else:
                    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
                    run_tokens += estimate_tokens("x" * prompt_chars) + estimate_tokens("x" * generated_chars)
```
(Simpler: `run_tokens += estimate_tokens_count(prompt_chars) + estimate_tokens_count(generated_chars)` — but since `estimate_tokens` takes text, either build a string or add a small `ceil(n/4)`. Prefer adding `from math import ceil` and `run_tokens += ceil(prompt_chars/4) + ceil(generated_chars/4)` to avoid allocating big strings. Implement whichever is clean; the test asserts tokens ≈ prompt/4 + generated/4.)

(g) Populate the result events. At all THREE result emits — error (`subtype="error"`, ~465), success (~717), max_turns (~770) — add `cost=_cost(run_tokens), tokens=run_tokens`. (For the error emit, `run_tokens` may be 0 if the failure was on the first call — that's fine.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_openai_cost_tracking.py -v` → PASS (3). Then regression: `pytest tests/test_openai_compat_deepseek.py tests/test_openai_backend_ids.py -q` → green (existing tests use rate 0; cost stays 0/None-compatible — if any asserts `result.cost is None`, it will now be `0.0`; update that assertion to `== 0.0` since cost is now always populated for this backend).

- [ ] **Step 5: Commit**
```bash
git add src/reverser/backends/openai_compat.py tests/test_openai_cost_tracking.py
git commit -m "feat(backends): OpenAI-compat token accounting + cost on result events"
```

---

## Task 4: create_backend forwards token_cost_per_1k

**Files:** Modify `src/reverser/backends/__init__.py`; extend `tests/test_backend_factory.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_backend_factory.py`:

```python
def test_create_backend_forwards_token_cost():
    from reverser.backends import create_backend
    be = create_backend("ollama", [], model="m", token_cost_per_1k=1.5)
    assert be._token_cost_per_1k == 1.5


def test_create_backend_token_cost_defaults_zero():
    from reverser.backends import create_backend
    be = create_backend("ollama", [], model="m")
    assert be._token_cost_per_1k == 0.0
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_backend_factory.py -k token_cost -v` → FAIL (`create_backend` has no such param).

- [ ] **Step 3: Implement** — in `__init__.py`, add `token_cost_per_1k: float = 0.0` to `create_backend`'s signature, and pass it through to the OpenAICompatBackend construction:
```python
    return OpenAICompatBackend(
        tools, model=model, api_base=api_base, model_family=model_family,
        token_cost_per_1k=token_cost_per_1k,
    )
```
(The `claude` branch returns before this and ignores the param.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_backend_factory.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/reverser/backends/__init__.py tests/test_backend_factory.py
git commit -m "feat(backends): create_backend forwards token_cost_per_1k"
```

---

## Task 5: SessionConfig field + dispatch forwarding

**Files:** Modify `src/reverser/sessions.py`, `src/reverser/tools/dispatch.py`; extend `tests/test_session_resume.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_session_resume.py`:

```python
def test_session_config_token_cost_round_trip():
    from reverser.sessions import SessionConfig
    from dataclasses import asdict
    c = SessionConfig(profile="general", token_cost_per_1k=0.75)
    assert SessionConfig(**asdict(c)).token_cost_per_1k == 0.75


def test_session_config_token_cost_default_zero():
    from reverser.sessions import SessionConfig
    assert SessionConfig(profile="general").token_cost_per_1k == 0.0
    # old snapshot without the key still loads
    c = SessionConfig(profile="general", backend="claude", model=None, api_base=None,
                      budget=5.0, max_turns=50, max_parallel=1)
    assert c.token_cost_per_1k == 0.0
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_session_resume.py -k token_cost -v` → FAIL (`unexpected keyword argument`).

- [ ] **Step 3: Implement**
(a) In `sessions.py` `SessionConfig`, add after `api_base` (or near `budget`):
```python
    token_cost_per_1k: float = 0.0
```
(b) In `dispatch.py`, find where the dispatched sub-agent backend is created (`create_backend(cfg.backend, ALL_TOOLS, model=cfg.model, api_base=cfg.api_base)` — read the exact call, it's in `dispatch_specialist`'s local-backend branch) and add `token_cost_per_1k=getattr(cfg, "token_cost_per_1k", 0.0)` to it. (Use `getattr` defensively in case `cfg` is ever a minimal stub.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_session_resume.py -k token_cost -v` → PASS. Then `pytest tests/test_dispatch.py -q` → green (dispatch still builds its backend; the new kwarg is accepted).

- [ ] **Step 5: Commit**
```bash
git add src/reverser/sessions.py src/reverser/tools/dispatch.py tests/test_session_resume.py
git commit -m "feat(sessions): SessionConfig.token_cost_per_1k + dispatch forwarding"
```

---

## Task 6: CLI flag → SessionConfig → backend

**Files:** Modify `src/reverser/cli.py` (+ session-construction seam); add/extend a CLI test.

- [ ] **Step 1: Write the failing test** — READ `cli.py` first to find the interactive (`i`) subparser and how `args.backend`/`args.model`/`args.api_base` reach the constructed `SessionConfig`/`AgentSession` (the Gap-2 `--validation-backend` flag was wired the same way — mirror it). Add a test in the file that tests CLI parsing (`tests/test_cli.py` or `tests/test_cli_sessions.py`):

```python
def test_cli_parses_token_cost_flag():
    from reverser.cli import build_parser   # use the REAL parser-factory name in cli.py
    args = build_parser().parse_args(["i", "10.0.0.1", "--token-cost-per-1k", "0.5"])
    assert args.token_cost_per_1k == 0.5
```
(If cli.py has no parser factory, target the real seam that maps args→SessionConfig, exactly as the validation flags are tested. Do NOT invent a function.)

- [ ] **Step 2: Run to verify it fails** — `unrecognized arguments` / attribute error.

- [ ] **Step 3: Implement** — add to the interactive subparser, beside `--validation-backend`:
```python
    interactive_parser.add_argument("--token-cost-per-1k", type=float, default=0.0,
        help="Pseudo-USD cost per 1k tokens for local (OpenAI-compatible) backends, "
             "so token usage counts toward the budget. Default 0 (tokens tracked, no cost).")
```
Then thread `args.token_cost_per_1k` into the `SessionConfig` the interactive command builds — follow the exact path `--validation-backend` takes (the Gap-2 wiring). If the construction passes `validation_backend=...` into `AgentSession`/`SessionConfig`, add `token_cost_per_1k=args.token_cost_per_1k` alongside. Also confirm the session passes `cfg.token_cost_per_1k` into its `create_backend(...)` call — find where `AgentSession` builds its backend (grep `create_backend(` in `agent_session.py`) and add `token_cost_per_1k=self._snapshot.config.token_cost_per_1k` (or the equivalent cfg reference) so the live session backend tracks cost. (This is the load-bearing wire — without it the flag is inert.)

- [ ] **Step 4: Run to verify it passes** — the CLI test + `pytest tests/test_cli.py tests/test_cli_sessions.py -q` → pass.

- [ ] **Step 5: Commit**
```bash
git add src/reverser/cli.py src/reverser/agent_session.py tests/test_cli*.py
git commit -m "feat(cli): --token-cost-per-1k -> SessionConfig -> backend"
```

---

## Task 7: full regression

- [ ] **Step 1:** `mkdir -p logs`; `PYTHONPATH="$PWD/src" .venv-python -m pytest -q` → green (≤2 skipped). Likely migration: an existing openai-compat test asserting `result.cost is None` → now `0.0`; update to `== 0.0`.
- [ ] **Step 2:** `PYTHONPATH="$PWD/src" .venv-python -c "from reverser.backends.tokens import estimate_tokens, tokens_from_usage; from reverser.backends import create_backend; print('ok')"` → no import error.
- [ ] **Step 3:** Manual sanity (no live server needed): construct an OpenAICompatBackend with `token_cost_per_1k=3.0`, feed a fake stream with usage, assert the result event's cost/tokens (covered by Task 3 tests; just confirm the full suite passes).
- [ ] **Step 4:** commit any cleanup.

---

## Self-Review notes

- **Spec coverage:** token helpers (Task 1); AgentEvent.tokens (Task 2); per-turn accounting + usage→estimate + stream_options probe + cost/tokens on all 3 result emits (Task 3); factory forwarding (Task 4); SessionConfig + dispatch forwarding (Task 5); CLI + live-session backend wire (Task 6); regression (Task 7). All spec sections map.
- **Load-bearing wire:** Task 6 Step 3 must connect `cfg.token_cost_per_1k` into the live session's `create_backend(...)` — without it the whole feature is inert for real runs. Flagged explicitly.
- **Confirm-during-TDD:** the exact `create_backend(...)` call inside `dispatch_specialist` (Task 5) and inside `AgentSession` (Task 6); the real cli.py parser/construction seam (mirror `--validation-backend`); whether any existing test asserts `result.cost is None` for the openai backend (migrate to `== 0.0`); the run() control flow so the Task 3 test targets a result event carrying accumulated tokens (use long content / max_turns appropriately).
- **Type consistency:** `estimate_tokens(text)->int`, `tokens_from_usage(usage)->int`, `token_cost_per_1k: float` everywhere, `AgentEvent.tokens: int|None`, `_cost(tok)->float`. Consistent across tasks + tests.
- **No-op default:** rate 0.0 throughout → existing dollar behavior unchanged; tokens become visible only.
```
