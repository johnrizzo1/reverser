# Dispatch → KB Reconciliation — Design

> Status: approved design direction, ready for implementation planning
> Date: 2026-05-30
> Origin: debugging session. A dispatched specialist returned a thorough report whose
> `### KB writes` section listed 6 findings + a spawned hypothesis, but **none reached the KB**.

## Root cause (evidence-backed)

For engagement `10.129.11.158` (run after the schema-validation merge):
- `kb_add_finding` was called **0 times** anywhere in the session log (which captures dispatched-
  specialist tool calls via `log_dispatch_event`), with **0 validation rejections**.
- The specialist's report contained a prose `## Findings` and `## KB writes` section listing 6
  findings, but it never invoked `kb_add_finding`. The 3 hypotheses that *did* persist were written
  by the **manager** (which owns the hypothesis tree); findings have no such owner.
- DB confirms: `findings` count = 0 for the target (and for every other target).

So: **specialists describe findings/KB-writes in prose but do not call the persistence tools, and
nothing reconciles the report's contents into the KB.** Not caused by the validation work (nothing
was rejected). Our `DispatchReportModel` already captures `findings[]`/`kb_writes[]` as validated
structured fields, but `dispatch_specialist` discards them (uses only `hypothesis_outcome` +
`status`) — which is the clean hook for the fix.

## Goals

- Findings (and spawned hypotheses) a specialist reports **reliably reach the KB** for the dispatch
  target and show up live in the GUI tabs.
- Robust across model quality (Claude and local models): a primary "specialist persists it" path
  plus a deterministic backstop.
- Reuse the existing validated `FindingModel` / KB write + emit paths; no cross-target pollution.

## Non-goals

- No change to the manager's hypothesis-tree ownership / K-failure pivot discipline.
- No re-running specialists; reconciliation works from the already-returned validated report.

## Decisions (from the debugging dialogue)

- **Approach:** Hybrid — contract mandate (primary) + dispatch-layer backstop (guarantee).
- **Backstop scope:** findings **and** spawned hypotheses.
- **Backstop marking:** findings persisted from a report without attached evidence are stored
  **unvalidated** (via `evidence_blocker`, `validated=False`, reachability clamped) with a note that
  they came from a dispatch report.

## Design

### 1. Enrich the report contract (schemas/models.py)

Add two structured sub-models and change `DispatchReportModel`:

- `ReportFinding` (LENIENT — specialist reports may omit detail):
  `title` (req, non-empty), `severity` (Severity enum, default `info`), `description` (req,
  non-empty), `reproduction` (opt str), `reachability` (opt Reachability), `confidence` (opt int
  0–100), `evidence_paths` (opt list[str]), `evidence_blocker` (opt str).
- `ReportHypothesis`: `statement` (req, non-empty), `rationale` (opt str), `confidence` (opt int
  0–100), `tags` (opt list[str]).
- `DispatchReportModel`:
  - `findings: list[ReportFinding]` (was `list[str]`).
  - new `hypotheses: list[ReportHypothesis] = []`.
  - keep `kb_writes: list[str]` and `follow_up: list[str]` as human-readable summary fields;
    `tldr`, `hypothesis_outcome`, `status` unchanged. `extra="ignore"` retained (tolerant parsing).

`parse_dispatch_report` is unchanged in shape — it still returns `(outcome, model, errors)`; the
model now carries structured findings/hypotheses.

### 2. Mandate (primary path) — `_RETURN_CONTRACT` + specialist prompts

Rewrite the contract so the specialist:
- MUST call `kb_add_finding` (and `kb_add_hypothesis` for new hypotheses) **before** writing the
  report — persistence is a required step, not a description.
- Emits the JSON block with `findings` as **objects** matching `ReportFinding` and a `hypotheses`
  array, mirroring what it persisted.
The JSON schema example in the contract is updated accordingly.

### 3. Backstop (guarantee) — `dispatch.py`

New pure-ish helper `reconcile_report_to_kb(kb, report_model) -> list[str]` (returns a list of
human-readable reconciliation actions for the dispatch envelope). Called from `dispatch_specialist`
after a valid parse (`report_model is not None`), against the **dispatch target's** KB:

- **Findings:** for each `ReportFinding`, dedup by normalized title (lowercase/stripped/whitespace-
  collapsed) against `kb.get_findings()`. If new:
  - Map `ReportFinding` → `FindingModel` via `validate_args`, filling required gaps for a degraded
    record: `reproduction = rf.reproduction or "(reported via specialist dispatch; reproduction not provided)"`,
    `confidence = rf.confidence if not None else 25`, `reachability = rf.reachability or unknown`.
  - If no `evidence_paths`, set `evidence_blocker = "reported via specialist dispatch ({specialty}); evidence not attached"`
    → model stores `validated=False` and clamps reachability ≤ theoretical.
  - Persist via `kb.record_finding(...)` + `emit_recorded_finding("create", fid, fact)`.
  - Also drop a `kb.record_note(...)` once per dispatch noting backstop reconciliation occurred.
- **Hypotheses:** for each `ReportHypothesis`, dedup by normalized statement against
  `kb.list_hypotheses()` (covers both manager-created and prior). If new: `kb.add_hypothesis(...)` +
  `emit_hypothesis("create", h)`. (Manager still owns status transitions; backstop only *creates*
  missing ones at `proposed`.)
- **Dedup vs the specialist's own calls:** because dedup is by title/statement against the live KB,
  a finding the specialist already persisted via `kb_add_finding` is skipped — no double-write.
- **Target safety:** reconciliation uses the dispatch's resolved engagement target only.

### 4. Envelope + emit

The dispatch result envelope gains a short "Reconciled to KB" line listing what the backstop wrote
(or "all report items already present"). Emits use the existing `emit_recorded_finding` /
`emit_hypothesis`, which resolve `current_session` — during `dispatch_specialist` that is the
manager's GUISession, so the Findings/Hypotheses tabs update live.

## Edge cases

- **No valid JSON / report_model is None:** no backstop (nothing structured to persist); existing
  degrade-to-partial behavior unchanged. (The mandate still nudges the specialist; future repair
  loop already tries to recover the JSON block.)
- **Empty findings/hypotheses arrays:** no-op.
- **FindingModel validation fails even after gap-filling** (e.g. title too long): skip that finding,
  record it in the reconciliation summary as skipped-with-reason; never raise out of dispatch.
- **Duplicate titles within one report:** dedup within the batch too (track seen titles).
- **Legacy DBs without v3 columns:** reconciliation opens the KB via `for_target`, which runs
  `apply_schema` (migration) first, so columns exist before writing.

## Testing strategy

- **Models:** `ReportFinding`/`ReportHypothesis` validation; `DispatchReportModel` parses structured
  findings + hypotheses; update the Task 9a tests that used `findings=["..."]` to the object shape.
- **`reconcile_report_to_kb`** (unit, temp KB):
  - new findings persisted with `validated=False` + evidence_blocker note when no evidence;
  - findings with full fields + evidence persisted normally (validated=True);
  - dedup: a finding whose title already exists is skipped (no duplicate row);
  - new hypotheses created at `proposed`; existing statement skipped;
  - returns an accurate action summary; emits fire (assert via a captured bus or monkeypatched
    emit).
- **parse_dispatch_report** updated for the object `findings` shape.
- **Full regression** green, including dispatch/manager suites.

## Affected files (anticipated)

- `src/reverser/schemas/models.py` (+ `__init__.py` exports): `ReportFinding`, `ReportHypothesis`,
  `DispatchReportModel.findings`/`hypotheses`.
- `src/reverser/tools/dispatch.py`: `_RETURN_CONTRACT`, `reconcile_report_to_kb`, wiring in
  `dispatch_specialist`, envelope line.
- Tests: `tests/schemas/test_models.py`, `tests/test_dispatch_helpers.py`, plus a new
  reconciliation test module.
