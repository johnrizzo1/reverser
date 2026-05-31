# API Error Resilience (Retry/Backoff + Quota Handling) — Design

> Status: approved design, ready for implementation planning
> Date: 2026-05-31
> Origin: Gap 4 of [docs/AUDIT_GAP_ANALYSIS.md](../../AUDIT_GAP_ANALYSIS.md). Today an LLM API error
> (429/503 overload, rate-limit/quota, connection blip) is unhandled: the OpenAI-compat backend
> catches all exceptions generically with no retry, and the Claude backend doesn't wrap `query()` or
> handle the SDK's `RateLimitEvent` at all — so a transient blip aborts the turn (or crashes with a
> raw traceback) on a long autonomous run.

## Goals

- Transient LLM API errors recover automatically (retry with exponential backoff) **without
  re-executing already-run tools**.
- Terminal errors (quota rejected, auth) fail fast with a clear, classified message — no pointless
  retries, no raw traceback.
- A terminated turn stays **resumable** (the session already autosaves per turn).

## Non-goals

- No whole-`query()` replay for the Claude backend (would re-run non-idempotent offensive tools).
- No change to the agent loop / session consumers (they already handle `error`/`result` events).
- No new dependency (`openai` is already a dep; `anthropic` is transitive).

## Key constraint (why the strategy is asymmetric)

- **OpenAI-compat backend** drives the agent loop itself: it calls
  `self._client.chat.completions.create()` per turn. Tools run *after* the model responds, so
  retrying just the `create()` call re-runs nothing — a clean, safe retry seam.
- **Claude backend** delegates the entire agentic loop to `claude_agent_sdk.query()` (which runs the
  Claude Code CLI subprocess, with its own internal HTTP retries). We cannot retry an individual
  model request; wrapping the whole `query()` would re-run every tool. So Claude-side resilience is
  *event handling + clean failure surfacing*, not retry.

## Decisions (from brainstorming)

- **Scope:** asymmetric — OpenAI-compat gets real per-request retry+backoff; Claude gets
  `RateLimitEvent` handling + a `try/except` around `query()` (no auto-replay).
- **Backoff policy (OpenAI-compat):** up to **3 retries**, `delay = min(30, 2 * 2**attempt)` →
  ~2s, 4s, 8s (cap 30s) + small jitter; emit a `llm_status` "retrying in Ns (attempt k/3)" event.
- **Terminal/exhausted:** emit one classified `error` event + a `result` error, end the turn
  gracefully (resumable). Terminal errors skip retries. Subtypes: `rate_limited` (429 exhausted),
  `quota_exhausted` (Claude `RateLimitEvent` rejected, or 401/403 auth), `api_error` (everything else).

## Design

### 1. Shared retry core — `src/reverser/backends/retry.py`

- `classify_error(exc) -> "transient" | "terminal"` — duck-typed on the `openai` exception surface
  (imported lazily). transient: `RateLimitError` (429), `APIConnectionError`, `APITimeoutError`,
  `APIStatusError` with status ≥ 500 or in {408, 409}. terminal: `APIStatusError` 400/401/403/404;
  **anything unrecognized → terminal** (fail safe — never loop on unknown errors).
- `async def call_with_retries(make_call, *, max_retries=3, base_delay=2.0, cap=30.0, on_retry=None,
  sleep=asyncio.sleep)`:
  - `await make_call()`; on exception → `classify_error`; **terminal → re-raise immediately**;
    transient with attempts remaining → `delay = min(cap, base_delay * 2**attempt)` + jitter,
    `on_retry(attempt, delay, exc)`, `await sleep(delay)`, retry; on exhaustion → re-raise last exc.
  - `sleep` and `on_retry` are injectable (tests don't wait; backend emits status events).
- This module is the single fully-unit-testable unit (classification + backoff math + control flow),
  with no backend coupling.

### 2. OpenAI-compat backend — `src/reverser/backends/openai_compat.py`

Replace the current bare `try: create() except Exception: yield error; return` with a retried call:
- `_make_call()` = `await self._client.chat.completions.create(model=..., messages=messages, ...)`.
- `on_retry(attempt, delay, exc)` appends a message to a small list the loop drains into
  `AgentEvent(kind="llm_status", content="API error (<Type>); retrying in <delay>s (attempt k/3)")`
  events right after the retried call returns (the callback can't `yield` from inside the generator).
- On exception out of `call_with_retries` (terminal OR exhausted): `yield AgentEvent(kind="error",
  content=msg, subtype=_subtype_for(exc), is_error=True)` then `yield AgentEvent(kind="result",
  content=f"Error: {msg}", subtype="error")` then `return`.
- `_subtype_for(exc)`: 429 → `rate_limited`; 401/403 → `quota_exhausted`; else `api_error`.
- **Preserve** the existing context-window special-case (`n_keep`/`n_ctx` helpful message) as a
  **terminal** error (must not be retried) — classify it terminal before/within the retry wrapper.

### 3. Claude backend — `src/reverser/backends/claude.py`

Inside `run()` (no whole-`query()` retry):
- **Handle `RateLimitEvent`** in the message loop. Import from `claude_agent_sdk` if exported, else
  detect via `hasattr(message, "rate_limit_info")`:
  - `status == "rejected"` → `yield error(subtype="quota_exhausted")` + `result` error + `return`.
  - `status == "allowed_warning"` → `yield AgentEvent(kind="llm_status", content="Approaching Claude
    API rate limit.")` and continue.
  - `status == "allowed"` → ignore.
- **Wrap** `async for message in query(...)` in `try/except Exception as e`: on failure (after the
  CLI's own internal retries) → `yield error(subtype="api_error", content=f"Claude backend error: {e}")`
  + `result` error + `return`. No raw exception escapes.

### 4. Surfacing

No consumer changes required. `agent_session.send()` / `agent.run_agent()` already handle `error`
and `result` events; the new `subtype` rides along for display/logging, and the per-turn snapshot
autosave keeps a terminated turn resumable. `llm_status` "retrying…" events render as non-fatal
status. No raw tracebacks reach the user.

## Testing strategy

- **`retry.py`** (injected `sleep`, fake exceptions mirroring the `openai` surface):
  - `classify_error`: 429/conn/timeout/5xx/408/409 → transient; 400/401/403/404 → terminal; unknown → terminal.
  - `call_with_retries`: succeed-on-2nd-attempt (returns, `on_retry` once, `sleep` with backoff delay);
    persistent transient → exhausts after `max_retries` then re-raises; terminal → re-raises immediately,
    `sleep` never called; backoff `min(cap, base*2**attempt)` + jitter within bounds.
- **OpenAI-compat** (fake `create`): two 429s then a normal stream → run completes + emits `llm_status`
  "retrying" events; persistent 429 → one `error` `subtype="rate_limited"` + `result` error; a 401 →
  terminal, **zero** retries; `n_keep/n_ctx` context-window error → terminal with helpful message (not retried).
- **Claude** (fake `query` async-gen + fake RateLimitEvent): `rejected` → `quota_exhausted` then clean
  return; `query()` raising mid-iteration → single `api_error` event, no exception escapes;
  `allowed_warning` → `llm_status` heads-up then continues.
- Full `pytest` green.

## Affected files

- New: `src/reverser/backends/retry.py`; tests `tests/test_backend_retry.py`,
  `tests/test_openai_backend_retry.py`, `tests/test_claude_backend_resilience.py`.
- Modify: `src/reverser/backends/openai_compat.py` (wrap `create()` + status/error events),
  `src/reverser/backends/claude.py` (RateLimitEvent handling + `query()` try/except).
