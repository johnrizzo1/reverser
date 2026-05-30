# Gap Analysis: `reverser` vs `evilsocket/audit`

> Reference comparison against [evilsocket/audit](https://github.com/evilsocket/audit), an
> AI-driven source-code vulnerability auditing tool that reimplements Cloudflare's
> "Project Glasswing" architecture. The goal is to identify orchestration and output-rigor
> patterns worth adopting in reverser.
>
> Date: 2026-05-30

## TL;DR

- **The Claude Code Agent SDK transfer is already done.** reverser uses `claude-agent-sdk`
  **0.2.82**; audit uses **0.0.20**. There are zero raw `anthropic` API imports in reverser's
  `src/`. reverser's integration is *richer* (91 custom MCP tools + a second OpenAI-compatible
  backend); audit has no custom tools at all. There is nothing to transfer on this axis.
- The real value in audit is its **orchestration discipline and output rigor**, not its SDK
  usage. Five transferable gaps are listed below, ranked by payoff.

## What the two projects actually are

| | `audit` | `reverser` |
|---|---|---|
| Problem | White-box **source-code vulnerability auditing** of a repo | Grey/black-box **offensive ops** (RE, network, web, AD, exploit) against binaries and live hosts |
| Agent tools | Built-in CLI only: Read/Grep/Glob/Bash | 91 custom MCP tools via `@tool` + `create_sdk_mcp_server` |
| LLM SDK | `claude-agent-sdk` 0.0.20 (`ClaudeSDKClient`) | `claude-agent-sdk` 0.2.82 (`query()`) + OpenAI-compat backend |
| Local models | None (Claude only) | Ollama / LM Studio / vLLM |
| State | Run-scoped SQLite (runs/tasks/findings) | Per-target persistent KB (hosts/services/creds/findings/hypotheses) + session snapshots |
| Interface | Batch CLI | Autonomous CLI + interactive TUI + Electron GUI |

These are **different problems**. Audit's domain tools don't transfer (it has none). Its
**process architecture** does.

## Transferable gaps (ranked by payoff)

### Gap 1 — Schema-validated structured outputs + in-session repair  ⭐ highest value

- **audit:** Every stage emits JSON validating against a Draft-7 JSON Schema (`schemas/`, 9
  files). The schema body is appended to the system prompt; output is validated; on failure a
  **repair turn** is issued in the same session (`runner.py` repair loop). Findings, traces,
  and the final report are all machine-checked contracts.
- **reverser:** No `jsonschema` / `.schema.json` anywhere in `src/`. Findings are free-text prose
  plus whatever the agent chooses to write via `kb_add_finding`. No enforced contract that a
  finding has severity / evidence / reproduction / reachability, and no repair loop.
- **Lesson:** reverser's KB (`FindingFact`, `HypothesisFact`) is already structured storage — it's
  ~80% there. Wrap finding/report emission in a JSON-Schema validate+repair loop to make KB writes
  reliable, reportable, and diffable. Highest-leverage item.

### Gap 2 — Adversarial validation via deliberate model disagreement

- **audit:** Hunt runs on Sonnet (wide, noisy); **Validate runs on Opus with a prompt whose job is
  to *disprove* each finding.** Different model = different blind spots = false positives filtered.
  Per-stage model selection is config-driven (`stages.yaml`).
- **reverser:** One model per session. Hypotheses are confirmed/refuted by the *same* agent that
  proposed them — no independent adversary, no model diversity.
- **Lesson:** add a validation pass where a *second* agent (ideally a different model — multi-backend
  already supported) tries to refute a finding/hypothesis before it is promoted to `confirmed`. The
  `dispatch_specialist` plumbing makes this feasible.

### Gap 3 — Reachability / "proof gate" before reporting

- **audit:** A dedicated **Trace** stage proves attacker-controlled input reaches the vulnerable
  sink; unreachable findings are dropped or downgraded. Nothing reaches the report without a
  provable path.
- **reverser:** Findings are reported on discovery; no gate requiring a demonstrated exploit path.
- **Lesson:** for an offensive tool this maps to "did we actually demonstrate the exploit / reach the
  asset." Gate `confirmed` findings on demonstrated reachability.

### Gap 4 — API error resilience (quota vs transient, retry with backoff)

- **audit:** `runner.py` classifies API failures — `_QUOTA_MARKERS` (terminal, abort cleanly +
  resumable) vs `_TRANSIENT_MARKERS` (529/503/overloaded → exponential backoff, up to 3 retries,
  30→60→120s).
- **reverser:** No backoff/retry/529 handling in `backends/`. `_conn_breaker.py` handles *target*
  connection failures, not Anthropic API errors. A transient 529 or quota exhaustion mid-engagement
  surfaces as a hard error.
- **Lesson:** wrap both backends' loops with transient-vs-terminal classification + exponential
  backoff.

### Gap 5 — Pipeline-level orchestration, concurrency, and budget gating

- **audit:** Work is a DAG of stages (recon → hunt → validate → gapfill → dedupe → trace → feedback
  → report) with `asyncio.Semaphore`-bounded fan-out (up to 50 concurrent hunters), per-stage *and*
  per-task budget checks that abort cleanly at `--max-cost-usd`, and SQLite run/task/finding state
  enabling `--resume`.
- **reverser:** Orchestration is a single manager driving `dispatch_specialist`
  (`tools/dispatch.py`, ~631 lines) — capable but ad-hoc and conversation-driven, not a declared
  task graph. `max_budget_usd` is passed to the SDK, but the OpenAI-compat backend tracks no cost,
  and there is no pipeline-wide budget gate to halt a runaway multi-specialist engagement.
- **Nuance:** reverser's session-snapshot resume (`sessions.py`) is genuinely good and arguably
  better than audit's for *interactive* use. The gap is specific to *autonomous, parallel,
  budget-bounded* runs.

## Where reverser is already ahead (do not regress)

1. **Multi-backend / local models** — audit is Claude-only.
2. **91 real custom MCP tools** — audit's agents only read code + run bash.
3. **Persistent per-target KB** vs audit's run-scoped state.
4. **Scope enforcement** — `scope.toml` (CIDR, no-DoS, no-account-lockout, allowed-hours). Audit only
   restricts network egress.
5. **Interactive TUI + Electron GUI** with live WebSocket updates — audit is batch CLI only.
6. **Tool allowlist enforced at dispatch** — prevents the model inventing out-of-profile tools.
7. **Broader domain** — binary RE + network + web + AD + exploit vs audit's source-only auditing.

One conscious choice to weigh, not copy: audit uses `permission_mode="acceptEdits"` (never bypass);
reverser uses `"bypassPermissions"`. Deliberate for autonomous offense, but worth an explicit
decision.

## Recommended priority order

| Priority | Item | Effort | Why |
|---|---|---|---|
| 1 | Schema-validated findings/reports + repair loop (Gap 1) | M | Builds on existing KB; biggest reliability/quality win |
| 2 | API retry/backoff + quota handling (Gap 4) | S | Cheap; directly improves long-run stability |
| 3 | Adversarial second-model validation (Gap 2) | M | Multi-backend + dispatch plumbing already exist |
| 4 | Reachability/proof gate before "confirmed" (Gap 3) | M | Credibility of output |
| 5 | Task-graph orchestration + budget gate + local-backend cost tracking (Gap 5) | L | Largest; for autonomous/parallel mode |

## Key file references

### reverser
- `src/reverser/backends/claude.py` — Claude Agent SDK integration (`query`, `ClaudeAgentOptions`, `create_sdk_mcp_server`)
- `src/reverser/backends/openai_compat.py` — local-model backend
- `src/reverser/backends/base.py` — `Backend` ABC + `AgentEvent`
- `src/reverser/tools/dispatch.py` — `dispatch_specialist` orchestration
- `src/reverser/kb/store.py`, `src/reverser/kb/schema.py` — persistent KB
- `src/reverser/sessions.py` — session snapshots / resume
- `src/reverser/prompts.py` + `src/reverser/profiles/*.py` — system prompts & 15 profiles

### audit
- `audit/runner.py` — `ClaudeSDKClient` wrapper, schema validation, repair loop, error classification
- `audit/orchestrator.py` — 8-stage pipeline driver + budget gates
- `audit/state.py` — SQLite DAO (runs/tasks/findings/traces/costs)
- `audit/json_utils.py` — JSON extraction + Draft-7 schema validation with `$ref` registry
- `config/stages.yaml` — per-stage model/concurrency/tools/max_turns
- `schemas/*.schema.json` — 9 structured-output contracts
- `prompts/*.md` — 8 stage system prompts
