# Reachability / Proof Gate — Design

> Status: approved design, ready for implementation planning
> Date: 2026-05-31
> Origin: Gap 3 of [docs/AUDIT_GAP_ANALYSIS.md](../../AUDIT_GAP_ANALYSIS.md). Findings already carry a
> self-reported `reachability` (demonstrated|likely|theoretical|unknown) and a `validated` flag, but
> the report renders them ALL flat and never surfaces reachability — a theoretical/unvalidated finding
> looks identical to a demonstrated, evidenced one. audit gates findings behind a reachability/"does
> attacker input actually reach the sink" proof before they count. This adds the offensive analogue:
> over-claiming is blocked at write time, and the report visibly tiers proven vs unproven findings.

## Goals

- A finding cannot claim `reachability="demonstrated"` without attached proof (evidence) — over-claims
  are hard-rejected at write time so the agent must attach evidence or lower reachability.
- The final report visibly separates **Confirmed** findings from **Unproven / Needs Verification**, and
  surfaces each finding's reachability + a validated marker, so the report's credibility reflects what
  was actually proven. No finding is dropped.

## Non-goals

- No reachability *tracing engine* (that's a much larger effort) — `reachability` stays self-reported.
- No change to severity/CVSS handling; the gate is about proof, not severity.
- No new DB column (reuses existing `reachability` + `validated`).

## Decisions (from brainstorming)

- **Both** a write-time tighten AND a report tier.
- **Write-time:** `demonstrated` without evidence → **hard reject** (consistent with the Gap-2 confirm
  gate's hard-block style).
- **Report tier line:** Confirmed = `reachability == "demonstrated" AND validated` ; everything else
  (likely / theoretical / unknown, or unvalidated, or legacy NULL) → Unproven.

## Design

### 1. Write-time gate — `FindingModel._check_evidence_or_blocker` (schemas/models.py)

The rule lives in the model's `@model_validator(mode="after")` (the single validation choke point —
so it applies to `kb_add_finding` and the dispatch backstop alike, surfacing through `validate_args`
as the tool's existing `is_error` hard reject). Current validator: empty evidence + no blocker →
raise; blocker path → `validated=False` + clamp reachability ≤ theoretical. **Add**, ordered so the
message is specific:

- If `reachability == Reachability.demonstrated` AND no non-empty `evidence_paths` AND no
  `evidence_blocker` → raise
  `ValueError("reachability='demonstrated' requires at least one evidence_paths entry — attach proof, lower reachability to 'likely'/'theoretical'/'unknown', or set evidence_blocker")`.
- The blocker path (which clamps to `theoretical`) is evaluated such that a blocker'd finding never
  trips the demonstrated rule (it's no longer demonstrated). The pre-existing "no evidence at all and
  no blocker" raise still covers non-demonstrated empty findings; the new check fires first for the
  demonstrated case to give the precise message.

`kb_add_finding` needs no change — it inherits the stricter model; `validate_args → format_error`
produces the hard reject. The dispatch reconciliation backstop already sets `evidence_blocker` +
`reachability="unknown"`, so it stays valid (clamped, unproven) — unaffected.

### 2. Report tiering — `_render_report` (tools/kb.py)

Replace the flat `## Findings` loop with a tiered split:

- **Classifier** `_is_confirmed(f) -> bool`: `(f.reachability == "demonstrated") and bool(f.validated)`.
  NOTE: `get_findings()` returns `FindingFact` with `reachability` as a plain **string** (DB stores
  `.value`), so compare against the string `"demonstrated"`, not the enum. `validated=None`/legacy
  `reachability=None` → not confirmed (safe default).
- **`## Confirmed Findings`** — findings passing the gate. Heading line gains a marker:
  `### [HIGH] <title>  ✓ demonstrated`, then CVSS / description / evidence as today.
- **`## Unproven / Needs Verification`** — everything else, with a ⚠ marker + reason:
  `### [MEDIUM] <title>  ⚠ theoretical (unvalidated)` / `⚠ likely` / `⚠ unknown`. Section preamble:
  "These findings are not yet proven — reachability is unconfirmed or evidence is missing. Verify
  before relying on them."
- **Stats line** (Engagement Statistics): `N finding(s) (C confirmed, U unproven)`.
- Each tier sorts by severity (critical→info) then title. Empty tier → `_None._`.
- `executive_summary` param and the rest of the report unchanged.

## Edge cases

- Legacy / NULL `reachability` or `validated` → Unproven (safe default).
- Enum-vs-string: write-time uses the `Reachability` enum; report reads plain strings — kept distinct.
- Write-time check ordering gives the demonstrated case its specific message before the generic
  empty-evidence raise.
- Dispatch backstop findings (blocker + unknown) remain valid and land in the Unproven tier.
- Nothing is dropped or suppressed; a critical-severity theoretical finding still appears (Unproven,
  severity shown).

## Testing strategy

- **Write-time (`tests/schemas/test_models.py`):** demonstrated + no evidence + no blocker →
  `ValidationError` mentioning "demonstrated"/"evidence"; demonstrated + evidence → passes,
  `validated=True`; demonstrated + evidence_blocker (no evidence) → clamps to theoretical,
  `validated=False` (no spurious demonstrated error); existing likely/theoretical/unknown-without-
  evidence tests stay green.
- **Tool (`tests/test_kb_tools.py`):** `kb_add_finding(reachability="demonstrated")` w/o evidence →
  `is_error` with the actionable message; with evidence → records.
- **Classifier unit:** `_is_confirmed` table — `("demonstrated",True)`→T; `("demonstrated",False)`→F;
  `("likely",True)`→F; `(None,True)`→F; `("unknown",True)`→F.
- **Report tiering:** seed temp KB with demonstrated+validated (evidence), theoretical, and legacy
  `reachability=None` findings → render → assert a `## Confirmed Findings` with only the first (`✓
  demonstrated`), a `## Unproven / Needs Verification` with the other two (`⚠`), stats `(1 confirmed,
  2 unproven)`; empty KB → both tiers `_None._`.
- **Full regression** green, incl. Gap-1 finding tests, Gap-2 confirm gate, dispatch reconciliation.

## Affected files

- Modify: `src/reverser/schemas/models.py` (FindingModel validator), `src/reverser/tools/kb.py`
  (`_is_confirmed` + tiered `_render_report` + stats line).
- Tests: extend `tests/schemas/test_models.py`, `tests/test_kb_tools.py`; a focused report-tier test.
- (No DB schema change; no new tool.)
