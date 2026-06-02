# Local-Backend Cost / Token Tracking (Gap 5-1) — Design

> Status: approved design, ready for implementation planning
> Date: 2026-05-31
> Origin: Gap 5 of [docs/AUDIT_GAP_ANALYSIS.md](../../AUDIT_GAP_ANALYSIS.md), first of the
> budget-gating sub-projects. The Claude backend reports real `cost` (SDK `total_cost_usd`); the
> OpenAI-compat backend (Ollama / LM Studio / vLLM / any OpenAI-compatible server) reports **no cost
> and no token usage**, so `SessionStats.total_cost` stays 0 on local runs and any future budget gate
> would be Claude-only. This adds token accounting + an opt-in dollar conversion to the OpenAI-compat
> backend, reusing the existing dollar plumbing so the later engagement-wide budget gate (Gap 5-3)
> works for local backends too.

## Goals

- The OpenAI-compat backend tracks **tokens per turn** and accumulates them across a run.
- Token count is **always surfaced** (on the result event) regardless of any cost rate — immediate
  visibility for local runs.
- An opt-in `token_cost_per_1k` rate converts tokens → a pseudo-USD `cost` on the existing result
  event, so `SessionStats.total_cost` and the future Gap 5-3 gate require no special-casing for local
  backends.

## Non-goals

- No budget *enforcement* / halt gate (that is Gap 5-3).
- No change to the Claude backend (it already reports real cost; `token_cost_per_1k` is ignored there).
- No separate token-budget axis — tokens convert into the single existing dollar axis.

## Decisions (from brainstorming)

- **Cost basis:** token-derived USD via a configurable `token_cost_per_1k` (default 0.0 = free). At
  rate 0 the dollar `cost` stays 0 but tokens are still tracked/surfaced.
- **Token source:** prefer the API `usage` block; fall back to a char/4 estimate when usage is absent
  (common for streaming local servers).

## Design

### 1. Token accounting (per turn, in `OpenAICompatBackend.run`)

- Request `stream=True, stream_options={"include_usage": True}` so compliant servers emit a final
  `usage` chunk. On any chunk carrying `usage`, capture `prompt_tokens + completion_tokens`.
- **Fallback estimate** when no usage arrived: `ceil(prompt_chars/4) + ceil(generated_chars/4)`, where
  `prompt_chars` = sum of the lengths of the `messages` content sent this turn, and `generated_chars`
  is already accumulated by the streaming loop. (char/4 ≈ standard rough token heuristic.)
- Accumulate `total_tokens` and `total_cost_usd` across turns within the run.
- `turn_cost_usd = tokens_this_turn / 1000 * self._token_cost_per_1k`.

### 2. Config → factory → backend wiring

- `OpenAICompatBackend.__init__` gains `token_cost_per_1k: float = 0.0` (after `model_family`), stored
  as `self._token_cost_per_1k`.
- `create_backend(...)` gains `token_cost_per_1k: float = 0.0`, forwarded to `OpenAICompatBackend`
  (ignored by `ClaudeBackend`).
- `SessionConfig` gains `token_cost_per_1k: float = 0.0` (serializable, backward-compatible — old
  snapshots load with the default). Plumbed from a new `reverser i --token-cost-per-1k` CLI flag, and
  passed wherever the session builds its backend (`create_backend(cfg.backend, ..., token_cost_per_1k=cfg.token_cost_per_1k)`).
- `dispatch_specialist` already builds its sub-agent backend via `create_backend(cfg.backend, ...)`;
  forward `cfg.token_cost_per_1k` there too so dispatched local specialists also report cost/tokens
  (consumed by the existing cost/turns accumulation in dispatch).

### 3. Events

- `AgentEvent` gains an optional `tokens: int | None = None` field (purely additive).
- Both result-emit sites in `run()` (`subtype="success"` and `subtype="max_turns"`) and the existing
  error/budget exits set `cost=total_cost_usd` and `tokens=total_tokens`.
- Claude backend continues to set real `cost` and leaves `tokens=None`.

## Edge cases

- No usage + no content (pure tool-call turn) → estimate from `prompt_chars` only; tokens never
  negative.
- `usage` present but partial/None numerics → treat missing as 0; if the usage total is 0, fall back
  to the estimate.
- Server rejects `stream_options` (400 mentioning `stream_options`) → one-time capability probe:
  catch that specific 400, retry the call once WITHOUT `stream_options`, then rely on the estimate.
  This is a capability fallback, not a transient retry — it must not interfere with the Gap-4
  `call_with_retries` wrapper (apply the probe inside the same make_call, or detect-and-disable on the
  first turn).
- rate = 0.0 (default) → `cost` stays 0.0 (unchanged dollar behavior); `tokens` still populated.
- Accumulation is per-run across turns, matching how `cost` is already consumed by `SessionStats` /
  dispatch.

## Testing strategy

Fake `chat.completions.create` returning a scripted async stream (the pattern the Gap-4 retry tests
use):
- usage present → `tokens` = prompt+completion from usage; `cost` = tokens/1000·rate.
- usage absent → estimate = `ceil(prompt_chars/4)+ceil(generated_chars/4)`; tokens ≈ estimate; cost matches.
- rate 0.0 → cost 0.0 but tokens > 0.
- rate > 0 → cost set on both `success` and `max_turns` result events.
- multi-turn run → tokens/cost accumulate.
- `stream_options`-400 path → falls back to a call without `stream_options` and still reports estimated tokens.
- `SessionConfig.token_cost_per_1k` round-trips (asdict/load); `create_backend(..., token_cost_per_1k=)`
  forwards to the backend; `reverser i --token-cost-per-1k` lands in `SessionConfig`.
- Full `pytest` green (existing openai-compat/deepseek tests use rate 0 → cost stays 0).

## Affected files

- Modify: `src/reverser/backends/base.py` (`AgentEvent.tokens`), `src/reverser/backends/openai_compat.py`
  (token accounting + result events + `__init__` param + stream_options probe),
  `src/reverser/backends/__init__.py` (`create_backend` param), `src/reverser/sessions.py`
  (`SessionConfig.token_cost_per_1k`), `src/reverser/tools/dispatch.py` (forward the rate),
  `src/reverser/cli.py` + session-construction path (CLI flag).
- Tests: extend `tests/test_openai_compat_deepseek.py` / a new `tests/test_openai_cost_tracking.py`,
  `tests/test_session_resume.py` (config round-trip), `tests/test_backend_factory.py`, a CLI test.
- (No DB/schema change. No budget enforcement — that's Gap 5-3.)
