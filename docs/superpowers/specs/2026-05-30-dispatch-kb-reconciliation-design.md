# Dispatch → KB Reconciliation — Design (markdown approach)

> Status: approved design, ready for implementation
> Date: 2026-05-30 (revised after the dispatch-repair-loop regression fix)
> Origin: debugging session. A dispatched specialist returned a thorough report whose
> `### KB writes` section listed 6 findings + a spawned hypothesis, but **none reached the KB**.

## Root cause (evidence-backed)

For engagement `10.129.11.158`: `kb_add_finding` was called **0 times** anywhere in the session log
(which captures dispatched-specialist tool calls), with **0 validation rejections**. The specialist's
report listed 6 findings under `## KB writes` (`- Finding: admin.snapped.htb hosts Nginx UI v2.3.2`,
…) but never invoked `kb_add_finding`. The manager only persists *hypotheses*, so findings have no
owner. DB confirmed `findings` = 0.

**Specialists describe findings/KB-writes in prose but do not call the persistence tools, and nothing
reconciles the report's contents into the KB.**

## Why markdown (not JSON)

A separate symptom in the same area — the dispatch "hang" — was traced to the (now-removed) Task 9c
emit+repair loop re-running specialists when they omitted the mandated JSON block. Specialists
reliably emit **markdown**, not JSON. So this reconciliation works from the **markdown report**:
`parse_dispatch_report` already recovers the outcome from markdown; this design adds
finding/hypothesis recovery from the markdown `### KB writes` section.

## Decisions (from the debugging dialogue)

- **Approach:** Hybrid — contract **mandate** (specialist calls the tools, primary) + dispatch-layer
  **backstop** that parses the markdown report and reconciles into the KB (guarantee).
- **Backstop scope:** findings **and** spawned hypotheses.
- **Backstop marking:** findings persisted from a report without attached evidence are stored
  **unvalidated** (via `evidence_blocker`, `validated=False`, reachability clamped) and noted as
  coming from a dispatch report. Hypotheses are created at `proposed` (manager still owns transitions).

## Design

### 1. Contract (`_RETURN_CONTRACT`) — mandate + parseable format

- **Soften the JSON block** from "MANDATORY" to "optional but recommended" (the repair loop that
  punished its absence is gone; outcome parsing falls back to markdown).
- **Mandate persistence:** "Before writing your report, persist each finding with `kb_add_finding`
  and each NEW hypothesis with `kb_add_hypothesis`."
- **Specify the `### KB writes` bullet format** so the backstop can parse it deterministically:
  - `- Finding: <short title> — <one-line description>`
  - `- Hypothesis: <statement>`
  (This matches the format specialists already emit naturally.)

### 2. Markdown parser (`dispatch.py`)

`parse_report_kb_writes(report_text) -> (findings, hypotheses)`:
- Locate the `### KB writes` section (regex `###\s+KB writes\s*\n(.+?)(?=\n###|\Z)`). If absent, also
  scan a `### Findings` section for finding bullets.
- For each bullet (`-`/`*` prefixed, optional `**bold**`):
  - `Finding:` prefix → `{"title": <text before ' — ' / ' - ', ≤120 chars>, "description": <full text>}`.
  - `Hypothesis:` / `Hypothesis spawned:` prefix → statement string.
- Returns `(list[dict], list[str])`. Pure function, unit-tested.

### 3. Reconcile (`dispatch.py`)

`reconcile_report_to_kb(kb, findings, hypotheses, *, specialty) -> list[str]` (returns action summary):
- **Findings:** dedup by normalized title (lowercase/stripped/whitespace-collapsed) vs
  `kb.get_findings()`. For each new one, build a `FindingModel` via `validate_args` with:
  `title` (≤120), `severity="info"`, `description`, `reproduction="(reported via specialist dispatch;
  reproduction not provided)"`, `confidence=25`, `reachability="unknown"`,
  `evidence_blocker=f"reported via {specialty} dispatch; evidence not attached"`. The blocker forces
  `validated=False` and clamps reachability. Persist via `kb.record_finding(...)` +
  `emit_recorded_finding("create", fid, fact)`. Skip-with-reason if validation still fails (never raise).
- **Hypotheses:** dedup by normalized statement vs `kb.list_hypotheses()`. New ones →
  `kb.add_hypothesis(statement=..., rationale=f"spawned via {specialty} dispatch", confidence=25)` +
  `emit_hypothesis("create", h)`, created at `proposed`.
- **Dedup vs the specialist's own calls:** because dedup is against the live KB (which already includes
  anything the specialist persisted via the mandate), real persisted items are skipped — no double-write.

### 4. Wiring + envelope

In `dispatch_specialist`, after `parse_dispatch_report`, call
`parse_report_kb_writes(report_text)` then `reconcile_report_to_kb(for_target(target), …, specialty=…)`
against the **dispatch engagement target**. Append a "Reconciled to KB" line to the result envelope
listing what the backstop wrote (or "all report items already present"). Emits resolve
`current_session` (the manager's GUISession during dispatch) so the Findings/Hypotheses tabs update live.

## Edge cases

- No `### KB writes`/`### Findings` section → no-op.
- Finding title > 120 chars → truncate.
- Duplicate titles within one report → dedup within the batch (track seen).
- `FindingModel` validation fails after gap-filling → skip that finding, note it; never raise out of
  dispatch.
- Legacy DBs → `for_target` runs the v3 migration on open, so columns exist before writing.
- Reconciliation runs only for the resolved engagement target (no cross-target writes).

## Testing strategy

- **`parse_report_kb_writes`** (unit): extracts `Finding:`/`Hypothesis:` bullets from `### KB writes`;
  tolerates `**bold**` and `—`/`-` separators; `### Findings` fallback; empty/missing section → empty.
- **`reconcile_report_to_kb`** (unit, temp KB):
  - new finding persisted with `validated=False` + evidence_blocker note;
  - dedup: a finding whose title already exists is skipped (no dup row);
  - new hypothesis created at `proposed`; existing statement skipped;
  - returns an accurate summary; emits fire (monkeypatched/captured).
- **Contract:** assert `_RETURN_CONTRACT` contains the persistence mandate + the bullet-format spec and
  no longer calls the JSON block "MANDATORY".
- **Full regression** green, including dispatch/manager suites.

## Affected files

- `src/reverser/tools/dispatch.py`: `_RETURN_CONTRACT`, `parse_report_kb_writes`,
  `reconcile_report_to_kb`, wiring in `dispatch_specialist`, envelope line.
- Tests: extend `tests/test_dispatch_helpers.py`; new reconciliation test module.
- (No schema-model changes needed — `FindingModel` is reused; `DispatchReportModel.findings` stays
  `list[str]`.)
