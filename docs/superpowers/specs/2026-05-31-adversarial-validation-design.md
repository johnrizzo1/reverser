# Adversarial Second-Model Validation — Design

> Status: approved design, ready for implementation planning
> Date: 2026-05-31
> Origin: Gap 2 of [docs/AUDIT_GAP_ANALYSIS.md](../../AUDIT_GAP_ANALYSIS.md). Today a hypothesis is
> confirmed/refuted by the *same* agent that proposed it — no independent adversary, no model
> diversity, so false positives can reach the final report. audit filters them with a separate
> Validate pass on a different model whose job is to *disprove* each finding.

## Goal

Before a hypothesis is promoted to `confirmed`, run a SECOND agent — ideally a DIFFERENT model — that
tries to **refute** it using the KB evidence. A refutation hard-blocks the confirm; an "upheld"/
"inconclusive" verdict lets it through. Every verdict is recorded. Opt-in: if no validator model is
configured, behavior is unchanged.

## Non-goals

- No validation of findings (no confirm step; out of scope — revisit later).
- No on-demand validation tool (the gate is automatic on the confirm transition).
- No KB schema change (verdict recorded in the free-form `evidence_refs` JSON + a note).
- The adversary does NOT run tools (read-only reasoning over provided evidence) — no re-execution of
  offensive tools, no KB mutation.

## Decisions (from brainstorming)

- **Scope:** hypotheses at the `confirmed` transition only.
- **Gate strength:** **hard block** — a `refuted` verdict makes `kb_update_hypothesis` return `is_error`;
  the agent must revise/gather more/choose another status. Verdict recorded either way.
- **Adversary model:** configured validator (`validation_backend`/`model`/`api_base`); if unset → skip
  the gate entirely (opt-in, no new hard dependency).
- **Fail-open:** if the adversary errors/times out, record a note and proceed (a validator outage must
  not block real work).

## Design

### 1. Config (opt-in) — `src/reverser/sessions.py`

`SessionConfig` gains three Optional fields (default `None`):
```
validation_backend: Optional[str] = None   # "claude" | "ollama" | "lmstudio" | ... ; None = OFF
validation_model:   Optional[str] = None
validation_api_base: Optional[str] = None
```
Serialize automatically via `asdict`; load is backward-compatible (`SessionConfig(**config_data)`
tolerates old snapshots → fields default `None`). Plumbed from new `reverser i` CLI flags
(`--validation-backend`, `--validation-model`, `--validation-api-base`) through to the session's
`SessionConfig`. GUI wiring is a documented follow-up — the gate only reads `config.validation_backend`.

### 2. Adversary module — `src/reverser/adversary.py`

```
@dataclass
class Verdict:
    verdict: str        # "refuted" | "upheld" | "inconclusive"
    reasoning: str
    model: str | None
    cost: float = 0.0
    turns: int = 0

async def run_adversary_validation(
    claim: str, evidence_text: str, *,
    backend_name: str, model: str | None, api_base: str | None,
    max_turns: int = 3, budget_usd: float = 0.10,
) -> Verdict
```
- One-shot, **read-only**: `create_backend(backend_name, ALL_TOOLS, model=…, api_base=…)` then
  `backend.run(prompt, system_prompt, max_turns=3, max_budget_usd=0.10, allowed_tools=[])`. No tool
  calls possible.
- **System prompt:** "You are a skeptical security reviewer. REFUTE the claim using ONLY the evidence
  provided — look for missing links, alternative explanations, unproven assumptions. If you genuinely
  cannot refute it, say so." User message embeds `claim` + `evidence_text`.
- **Output + parsing** (mirrors the robust dispatch parser): prefer a fenced ```json block
  `{"verdict": "refuted|upheld|inconclusive", "reasoning": "..."}`; fall back to a `VERDICT:` /
  `REASONING:` markdown line; unparseable → `inconclusive` (fail-open).
- **Verdict semantics:** `refuted` → gate blocks; `upheld`/`inconclusive` → proceed.
- Depends only on `create_backend` + `AgentEvent` consumption → unit-testable with a fake backend.

### 3. Gate wiring — `kb_update_hypothesis` (src/reverser/tools/kb.py)

After the existing transition validation, before persisting, when `new_status == "confirmed"`:
1. `sess = current_session.get()`; if `sess is None` or not `sess.config.validation_backend` → skip the
   gate, persist as today.
2. `evidence_text = _serialize_evidence_for_validation(kb, evidence_refs, hypothesis)` — dereference the
   supplied + existing `evidence_refs` via `kb.resolve_evidence_refs(...)` and render compact text
   (finding `title+severity+description`, note body, cred `user@domain (status)`, service `host:port
   svc`), plus the hypothesis `statement` + `rationale`; truncate per-item and cap total (~4 KB).
3. `verdict = await run_adversary_validation(claim=statement, evidence_text=…, backend_name=cfg.validation_backend,
   model=cfg.validation_model, api_base=cfg.validation_api_base)`, wrapped in try/except.
4. Decide:
   - **`refuted`** → `kb.record_note("Adversarial validation REFUTED hyp #N (model=…): <reasoning>")`
     and `return format_error("Adversarial validation refused the 'confirmed' transition: <reasoning>. "
     "Revise the hypothesis/evidence, gather more, or use status='testing'/'inconclusive'.")` —
     hypothesis NOT updated.
   - **`upheld` / `inconclusive`** → append `{"kind": "validation", "verdict": …, "model": …,
     "reasoning": …}` to `evidence_refs` (free-form; `resolve_evidence_refs` ignores unknown kinds),
     `record_note("Adversarial validation <verdict> hyp #N (model=…): <reasoning>")`, then the normal
     `kb.update_hypothesis(...)`. Success message notes the verdict.
   - **adversary raised** → `record_note("Adversarial validation unavailable (<err>); confirmed without it")`,
     proceed with the confirm (fail-open). Success message notes it.
Only the `confirmed` transition triggers this; `refuted`/`abandoned`/`testing`/`blocked` are untouched.

## Error handling

No validator / no session → skip. Adversary error/timeout → fail-open (note + proceed). Unparseable
verdict → `inconclusive` → proceed. Missing evidence rows → dropped by `resolve_evidence_refs`. Evidence
text capped (~4 KB). `allowed_tools=[]` → no KB mutation / tool re-execution.

## Testing strategy

- **`adversary.py`** (fake backend yielding scripted text): json-block `refuted`/`upheld`/`inconclusive`
  parsed; markdown `VERDICT:` fallback; unparseable → `inconclusive`; backend invoked with
  `allowed_tools=[]` (read-only); `Verdict` carries reasoning/model/cost.
- **Gate** (monkeypatch `run_adversary_validation` imported into kb.py; stub `current_session` with
  `config.validation_backend` set): refuted → confirm blocked (`is_error` + reasoning), status unchanged,
  note recorded; upheld → proceeds, `evidence_refs` gains the `validation` marker, note recorded, status
  `confirmed`; validator not configured → adversary NOT called, proceeds; adversary raises → fail-open,
  proceeds, "unavailable" note; non-confirmed transitions → adversary never called.
- **Config** — `SessionConfig` save→load round-trip preserves the three fields; old snapshots load with
  `None`.
- **CLI** — `reverser i --validation-backend … --validation-model …` lands in the session's
  `SessionConfig`.
- Full `pytest` green.

## Affected files

- New: `src/reverser/adversary.py`; tests `tests/test_adversary.py`, `tests/test_hypothesis_validation_gate.py`.
- Modify: `src/reverser/sessions.py` (SessionConfig fields), `src/reverser/tools/kb.py` (gate +
  `_serialize_evidence_for_validation`), `src/reverser/cli.py` + the session-construction path
  (`agent_session.py` / `session_start.py`) for the CLI flags; extend `tests/test_kb_hypotheses.py` /
  `tests/test_cli*.py` as needed.
- (No KB schema change. GUI config wiring deferred.)
