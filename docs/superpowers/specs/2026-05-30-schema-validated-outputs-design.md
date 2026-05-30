# Schema-Validated Structured Outputs — Design

> Status: approved design, ready for implementation planning
> Date: 2026-05-30
> Origin: Gap 1 of [docs/AUDIT_GAP_ANALYSIS.md](../../AUDIT_GAP_ANALYSIS.md) — adopt
> `evilsocket/audit`'s schema-validation + repair rigor, adapted to reverser's interactive,
> MCP-tool, multi-backend architecture.

## Problem

reverser's agent outputs are largely free-text plus loosely-validated KB writes. `FindingFact`
validates only `severity`; nothing requires evidence, reproduction, confidence, or reachability.
Hypothesis status changes are unconstrained. Specialist dispatch reports are parsed with regex
heuristics (`parse_hypothesis_outcome`, `_has_actionable_findings`) over free-text markdown. There
is no machine-checked contract on what the agent records, and no repair path when output is
malformed.

audit enforces a JSON Schema on every stage output, appends the schema to the prompt, and runs an
in-session repair turn on failure. We adopt that rigor, but at reverser's natural enforcement
points rather than as a batch-pipeline emit step.

## Goals

- Every finding, hypothesis (create + update), final report, and specialist dispatch report
  conforms to an explicit, validated contract.
- Invalid output is **rejected and the agent must resubmit a valid version** (max-rigor choice).
- A genuinely-stuck agent has a legal escape so it never burns the whole budget looping.
- One source of truth per output type; no dual schema maintenance.
- No changes to the backend agent loops; works identically on the Claude SDK and OpenAI-compat
  backends.

## Non-goals

- No adversarial second-model validation (that is Gap 2, a separate spec).
- No reachability *tracing* engine (Gap 3); `reachability` here is a self-reported, validated field.
- No pipeline/task-graph orchestration or budget gating (Gap 5).
- No DB migration tooling beyond additive columns.

## Decisions (from brainstorming)

- **Scope:** findings, hypotheses, final report, and specialist dispatch reports — all four.
- **Failure mode:** reject + force retry (maximum rigor).
- **Schema source:** Pydantic v2 models as the single source of truth (pydantic 2.13.4 already
  installed via FastAPI). Models drive both validation and the `@tool` input schema.
- **Approach:** C (full hybrid) — tool-boundary validation for incremental writes (findings,
  hypotheses, report export); bounded emit+repair for the dispatch return contract.
- `reproduction` and `reachability` are **required on every finding**, with the `evidence_blocker`
  escape handling cases where they do not apply (e.g. pure binary-RE observations).
- The Pydantic models **replace** `FindingFact` / `HypothesisFact` as the validation authority.
- The report's executive summary is an **agent-supplied, tool-boundary-validated argument** to the
  export tool — not an internally-spawned LLM call. (Bounded emit+repair genuinely applies only to
  dispatch, where reverser controls a sub-agent invocation.)

## Architecture

New package `src/reverser/schemas/`:

- **`schemas/models.py`** — one Pydantic v2 model per output type:
  `FindingModel`, `HypothesisModel`, `HypothesisUpdateModel`, `ReportModel`, `DispatchReportModel`.
  These supersede the hand-rolled dataclasses as the validation authority.
- **`schemas/validation.py`** — shared helpers:
  - `validate_args(Model, args) -> ValidationResult` — parses `args` into `Model`; on
    `pydantic.ValidationError` returns a result carrying an **actionable** error string (one line
    per problem: field path + constraint + the field's description), formatted for an LLM to fix.
  - `tool_input_schema(Model) -> dict` — `Model.model_json_schema()` adapted to the `@tool`
    input-schema shape: **`$ref`s inlined, enums/defaults flattened**, so the declared tool schema
    and validation come from the same model.

Two enforcement points, both reusing the models. **No edits to `backends/claude.py` or
`backends/openai_compat.py`.**

### Point A — tool boundary (findings, hypotheses, report export)

Handler flow:

```
result = validate_args(FindingModel, args)
if result.is_error:
    return format_tool_result(result.error_text, is_error=True)   # the rejection
kb.record_finding(result.value)
return format_tool_result("Finding recorded: ...")
```

- The `is_error=True` tool result is what the agent receives; its next turn resubmits a corrected
  call. **No internal retry counter** — the agent loop self-corrects; `max_turns` /
  `max_budget_usd` are the ceiling.
- Each affected tool's `@tool` input-schema dict is generated via `tool_input_schema(Model)`, so the
  LLM sees the exact contract up front (audit's "schema in the prompt" trick, automatic).
- Affected tools: `kb_add_finding`, `kb_add_hypothesis`, `kb_update_hypothesis`, `kb_export_report`.

### Point B — bounded emit+repair (dispatch only)

Inside `dispatch_specialist`, after the specialist sub-agent returns its payload:

```
for attempt in range(MAX_DISPATCH_REPAIR + 1):   # default 2
    result = validate_args(DispatchReportModel, payload)
    if not result.is_error:
        break
    payload = await rerun_specialist(repair_prompt(result.error_text))
else:
    # exhausted: accept degraded, status forced to "partial", errors embedded,
    # partial fields extracted from whatever validated
```

Replaces `parse_hypothesis_outcome` / `_has_actionable_findings`; `hypothesis_outcome` now comes
from the validated model. This is the **only** place degraded-accept exists, because a sub-agent
cannot be allowed to block the manager indefinitely.

### Blocker safety-valve (Point A loop guard)

Every model carries an optional escape field (`evidence_blocker` on findings; analogous `blocker`
on hypotheses). A record *with* a populated blocker **passes** validation but is stored flagged
`unvalidated`/degraded (a finding's `reachability` is forced to ≤ `theoretical`). A stuck agent
thus has a legal way to record and move on; the default path stays strict.

## Data contracts (Pydantic models)

### FindingModel (supersedes FindingFact)

| field | type | rule |
|---|---|---|
| `title` | str | required, non-empty, ≤120 chars |
| `severity` | enum | required: `info\|low\|medium\|high\|critical` |
| `description` | str | required, non-empty |
| `evidence_paths` | list[str] | ≥1 non-empty entries required, unless `evidence_blocker` is set. On-disk existence is **not** enforced (paths may be logical/remote); existence may be surfaced as a non-blocking warning only. |
| `reproduction` | str | required — how to reproduce/trigger |
| `confidence` | int | required, 0–100 |
| `reachability` | enum | required: `demonstrated\|likely\|theoretical\|unknown` |
| `cvss` | float? | optional, 0.0–10.0 |
| `evidence_blocker` | str? | optional escape; if set, record stored degraded and `reachability` clamped ≤ `theoretical` |

### HypothesisModel (supersedes HypothesisFact)

| field | type | rule |
|---|---|---|
| `statement` | str | required, non-empty |
| `rationale` | str | required |
| `confidence` | int | required, 0–100 |
| `parent_id` | int? | optional |
| `tags` | list[str] | optional |
| `blocker` | str? | optional escape |

### HypothesisUpdateModel

Enforces status transitions: `proposed → testing → {confirmed | refuted | abandoned}`. Reaching
`confirmed` or `refuted` requires ≥1 `evidence_refs`. Illegal jumps (e.g. `proposed → confirmed`
with no evidence) are rejected. Hardens the K-failure pivot discipline.

### DispatchReportModel (replaces regex parsing)

`tldr`, `findings[]`, `hypothesis_outcome` (`confirmed | refuted | inconclusive`), `kb_writes[]`,
`follow_up[]`, `status` (`success | partial | error`).

### ReportModel

Assembled from validated KB rows: `target`, `executive_summary` (agent-supplied, validated at the
`kb_export_report` boundary), `findings[]` (`FindingModel`), `hypotheses[]`, and `hosts`/`services`/
`creds` counts. Rendered to markdown by the existing `_render_report`.

## Error handling & edge cases

- **Retry-loop containment:** no internal counter on Point A; guards are the blocker escape and
  `max_turns`/`max_budget_usd`. Identical consecutive rejections are deduped in the session log so
  thrashing is visible.
- **Backend parity:** validation lives only in tool handlers + `dispatch.py`. The OpenAI-compat
  path text-parses tool args, so `validate_args` must tolerate stringified scalars
  (`"confidence": "80"`) — Pydantic v2 coerces by default; covered by explicit tests.
- **`tool_input_schema` fidelity:** `model_json_schema()` emits Draft-2020-12 with `$defs`/`$ref`;
  the MCP `@tool` schema must be self-contained, so the helper inlines `$ref`s and flattens enums/
  defaults. A test asserts no `$ref` survives.
- **Legacy rows:** models validate on **write only**. Read paths (`get_findings`, report render)
  tolerate legacy rows lacking new fields (defaults: `reachability=unknown`, `confidence=None`). New
  columns added if absent; no migration tool.
- **Empty report:** zero findings → valid empty report, not an error.
- **Dispatch degraded-accept:** `status="partial"`, validation errors embedded in the returned
  report body, partial fields extracted from whatever validated.
- **Ordering:** auth/scope checks continue to short-circuit *before* validation in the `kb_*` tools.

## Testing strategy

TDD, pytest. New `tests/schemas/`; extend `tests/tools/`.

1. **Model units** (`tests/schemas/test_models.py`): valid passes; each required field missing →
   rejected; enum/range violations rejected; `evidence_blocker` set → passes flagged degraded;
   hypothesis status-transition matrix (legal pass, `proposed→confirmed` w/o evidence rejected).
2. **Error rendering** (`test_validation.py`): one actionable line per error with field path +
   constraint + description; all errors surface, not just the first.
3. **`tool_input_schema`:** generated schema self-contained — no `$ref` survives, enums inlined,
   `required` correct, valid as a `@tool` input schema.
4. **Tool-boundary integration** (extend `tests/tools/test_kb*.py`): bad args → `is_error=True` +
   actionable text, nothing written; good args → recorded + success; legacy-row reads tolerate
   missing fields (no validate-on-read).
5. **Backend-parity/coercion:** stringified numbers from the OpenAI-compat path validate.
6. **Dispatch emit+repair:** invalid → re-prompt; valid on retry → success; exhausted → `partial`
   with errors embedded + partial extraction; `hypothesis_outcome` from the model, not regex.
7. **Regression:** full `pytest` green, including the in-flight `test_kb_emit.py` and
   `test_persona_alignment.py` changes in the working tree.

## Affected files (anticipated)

- New: `src/reverser/schemas/__init__.py`, `models.py`, `validation.py`
- Edit: `src/reverser/tools/kb.py` (finding/hypothesis/report tools), `src/reverser/tools/dispatch.py`
  (emit+repair, drop regex helpers), `src/reverser/kb/store.py` (record paths accept models; additive
  columns; tolerant reads)
- New tests under `tests/schemas/`; extended tests under `tests/tools/`
```
