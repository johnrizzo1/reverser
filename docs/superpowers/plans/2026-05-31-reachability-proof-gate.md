# Reachability / Proof Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop findings from over-claiming proof (a `reachability="demonstrated"` finding with no evidence is hard-rejected at write time), and tier the final report into Confirmed vs Unproven so its credibility reflects what was actually proven.

**Architecture:** Two changes, no DB/schema/tool additions. (1) `FindingModel`'s `@model_validator` gains a precise reject for `demonstrated`-without-evidence. (2) `_render_report` (tools/kb.py) splits the flat `## Findings` loop into `## Confirmed Findings` (demonstrated + validated) and `## Unproven / Needs Verification`, surfacing reachability + a ✓/⚠ marker, and the stats line reports the split.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest.

**Spec:** [docs/superpowers/specs/2026-05-31-reachability-proof-gate-design.md](../specs/2026-05-31-reachability-proof-gate-design.md)

**Test command:** `PYTHONPATH="$PWD/src" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.venv/bin/python -m pytest <args>`

---

## File Structure

- **Modify** `src/reverser/schemas/models.py` — `FindingModel._check_evidence_or_blocker`: add the demonstrated-needs-evidence reject.
- **Modify** `src/reverser/tools/kb.py` — add `_is_confirmed(f)` + tiered `_render_report` Findings section + split stats line.
- **Tests** — extend `tests/schemas/test_models.py`, `tests/test_kb_tools.py`; add a focused report-tier test.

**Verified facts (read on current `main`):**
- `FindingModel` validator (`models.py:62-76`): `non_empty = [p for p in self.evidence_paths if p and p.strip()]`; if empty: blocker-less → raise generic; else degraded path sets `validated=False` and clamps reachability ≤ theoretical via `_REACHABILITY_ORDER` (enum keys). `Reachability` enum: demonstrated/likely/theoretical/unknown.
- `FindingFact` (store.py): `reachability: Optional[str]` (plain string from DB), `validated: bool = True`, plus `title, severity, description, evidence_paths, cvss, reproduction, confidence, evidence_blocker`.
- `_render_report` (kb.py:687-783): `findings = kb.get_findings()`; stats line at ~713-716 (`{len(findings)} finding(s)`); flat Findings loop at 765-782 (`### [{f.severity.upper()}] {f.title}`, optional `_CVSS_`, description, `**Evidence:**` list).
- Existing severity order tuple `("critical", "high", "medium", "low", "info")` appears at kb.py:139 (reuse for sort).
- `kb_add_finding` validates via `FindingModel` and returns `format_error` on `ValidationError` (so the model reject surfaces as the tool's is_error).

---

## Task 1: write-time demonstrated-needs-evidence reject

**Files:** Modify `src/reverser/schemas/models.py`; extend `tests/schemas/test_models.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/schemas/test_models.py`:

```python
def test_demonstrated_without_evidence_is_rejected():
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    kw["reachability"] = "demonstrated"
    # no evidence_blocker -> hard reject with a demonstrated-specific message
    with pytest.raises(ValidationError) as ei:
        FindingModel(**kw)
    assert "demonstrated" in str(ei.value).lower()


def test_demonstrated_with_evidence_passes():
    kw = _valid_finding_kwargs()  # already demonstrated + evidence_paths
    m = FindingModel(**kw)
    assert m.reachability == Reachability.demonstrated and m.validated is True


def test_demonstrated_with_blocker_clamps_not_demonstrated():
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    kw["reachability"] = "demonstrated"
    kw["evidence_blocker"] = "target offline"
    m = FindingModel(**kw)
    # blocker path wins: clamped, unvalidated, no spurious 'demonstrated requires evidence' error
    assert m.validated is False and m.reachability == Reachability.theoretical


def test_likely_without_evidence_still_rejected_generically():
    # unchanged behavior: empty evidence + no blocker is still rejected (any reachability)
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    kw["reachability"] = "likely"
    with pytest.raises(ValidationError):
        FindingModel(**kw)
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/schemas/test_models.py -k demonstrated -v` → `test_demonstrated_without_evidence_is_rejected` fails (current generic message doesn't contain "demonstrated").

- [ ] **Step 3: Implement** — in `models.py`, change `_check_evidence_or_blocker` so the demonstrated case gets a specific message BEFORE the generic raise. Replace the existing method body:

```python
    @model_validator(mode="after")
    def _check_evidence_or_blocker(self) -> "FindingModel":
        non_empty = [p for p in self.evidence_paths if p and p.strip()]
        self.evidence_paths = non_empty
        if not non_empty:
            has_blocker = bool(self.evidence_blocker and self.evidence_blocker.strip())
            if not has_blocker:
                if self.reachability == Reachability.demonstrated:
                    raise ValueError(
                        "reachability='demonstrated' requires at least one evidence_paths "
                        "entry — attach proof, lower reachability to "
                        "'likely'/'theoretical'/'unknown', or set evidence_blocker"
                    )
                raise ValueError(
                    "evidence_paths must contain at least 1 entry, "
                    "or set evidence_blocker explaining why none exist"
                )
            # degraded path: flag + clamp reachability
            self.validated = False
            if _REACHABILITY_ORDER[self.reachability] > _REACHABILITY_ORDER[Reachability.theoretical]:
                self.reachability = Reachability.theoretical
        return self
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/schemas/test_models.py -v` → PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/schemas/models.py tests/schemas/test_models.py
git commit -m "feat(schemas): reject demonstrated reachability without evidence"
```

---

## Task 2: tool-level reject surfaces through kb_add_finding

**Files:** Extend `tests/test_kb_tools.py` (test only — Task 1's model change does the work).

- [ ] **Step 1: Write the test** — append to `tests/test_kb_tools.py` (match the file's auth/target/handler conventions used by the other `kb_add_finding` tests; `_handler`/`tmp_targets_dir`/`_check_auth`-patch are illustrative — copy the real ones from the file):

```python
@pytest.mark.asyncio
async def test_kb_add_finding_demonstrated_without_evidence_rejected(tmp_targets_dir, monkeypatch):
    import reverser.kb; reverser.kb._kb_cache.clear()
    import reverser.tools.kb as kbmod
    monkeypatch.setattr(kbmod, "_check_auth", lambda: None)
    fn = getattr(kbmod.kb_add_finding, "handler", None) or getattr(kbmod.kb_add_finding, "fn", None) or kbmod.kb_add_finding
    res = await fn({
        "target": "t1", "title": "RCE", "severity": "critical",
        "description": "d", "reproduction": "r", "confidence": 80,
        "reachability": "demonstrated", "evidence_paths": []})
    assert res.get("is_error") is True
    assert "demonstrated" in res["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_kb_add_finding_demonstrated_with_evidence_ok(tmp_targets_dir, monkeypatch):
    import reverser.kb; reverser.kb._kb_cache.clear()
    import reverser.tools.kb as kbmod
    monkeypatch.setattr(kbmod, "_check_auth", lambda: None)
    fn = getattr(kbmod.kb_add_finding, "handler", None) or getattr(kbmod.kb_add_finding, "fn", None) or kbmod.kb_add_finding
    res = await fn({
        "target": "t1", "title": "RCE", "severity": "critical",
        "description": "d", "reproduction": "r", "confidence": 80,
        "reachability": "demonstrated", "evidence_paths": ["findings/poc.txt"]})
    assert res.get("is_error") is not True
```

(Read the existing `kb_add_finding` tests in `tests/test_kb_tools.py` first and mirror their exact fixture/auth/handler pattern; adapt the calls above to match. The point is: demonstrated+no-evidence → is_error mentioning "demonstrated"; demonstrated+evidence → ok.)

- [ ] **Step 2: Run to verify it passes** — `pytest tests/test_kb_tools.py -k "demonstrated" -v` → PASS (Task 1's model already enforces it; this just locks in the tool surface).

(If it FAILS because `kb_add_finding` swallows/transforms the error differently, read the handler and adjust the assertion to match how it surfaces a `validate_args` failure — do NOT change handler behavior.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_kb_tools.py
git commit -m "test(kb): kb_add_finding rejects demonstrated-without-evidence"
```

---

## Task 3: report classifier `_is_confirmed`

**Files:** Modify `src/reverser/tools/kb.py`; add a test (in `tests/test_kb_tools.py` or a new `tests/test_report_tiers.py`).

- [ ] **Step 1: Write the failing test** — create `tests/test_report_tiers.py`:

```python
import pytest
from types import SimpleNamespace

from reverser.tools.kb import _is_confirmed


@pytest.mark.parametrize("reach,validated,expected", [
    ("demonstrated", True, True),
    ("demonstrated", False, False),
    ("likely", True, False),
    ("theoretical", True, False),
    ("unknown", True, False),
    (None, True, False),
    ("demonstrated", None, False),
])
def test_is_confirmed(reach, validated, expected):
    f = SimpleNamespace(reachability=reach, validated=validated)
    assert _is_confirmed(f) is expected
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_report_tiers.py -v` → `ImportError: cannot import name '_is_confirmed'`.

- [ ] **Step 3: Implement** — in `kb.py`, add this helper just ABOVE `_render_report`:

```python
def _is_confirmed(f) -> bool:
    """A finding is 'confirmed' for report tiering only when reachability is
    'demonstrated' AND it carries real evidence (validated). reachability is a
    plain string on FindingFact; None/legacy -> not confirmed."""
    return f.reachability == "demonstrated" and bool(f.validated)
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_report_tiers.py -v` → PASS (7).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_report_tiers.py
git commit -m "feat(kb): _is_confirmed classifier for report tiering"
```

---

## Task 4: tiered report rendering

**Files:** Modify `src/reverser/tools/kb.py` (`_render_report`); extend `tests/test_report_tiers.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_report_tiers.py`:

```python
def _seed_kb(tmp_path, monkeypatch, target="rt"):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb; reverser.kb._kb_cache.clear()
    from reverser.kb.store import KB, FindingFact
    kb = KB(target)
    kb.record_finding(FindingFact(title="Proven RCE", severity="critical",
        description="d", evidence_paths=["findings/poc.txt"], reproduction="r",
        reachability="demonstrated", confidence=90, validated=True))
    kb.record_finding(FindingFact(title="Maybe SQLi", severity="high",
        description="d", evidence_paths=[], reproduction="r",
        reachability="theoretical", confidence=40, evidence_blocker="no shell",
        validated=False))
    # legacy: reachability None
    kb.record_finding(FindingFact(title="Legacy item", severity="low",
        description="d", evidence_paths=["x"], reproduction="r"))
    return kb


def test_render_report_tiers_findings(tmp_path, monkeypatch):
    from reverser.tools.kb import _render_report
    kb = _seed_kb(tmp_path, monkeypatch)
    out = _render_report(kb)
    assert "## Confirmed Findings" in out
    assert "## Unproven / Needs Verification" in out
    # confirmed section has the demonstrated one with a check + reachability
    conf = out.split("## Confirmed Findings", 1)[1].split("## Unproven", 1)[0]
    assert "Proven RCE" in conf and "demonstrated" in conf
    assert "Maybe SQLi" not in conf and "Legacy item" not in conf
    # unproven section has the other two
    unp = out.split("## Unproven / Needs Verification", 1)[1]
    assert "Maybe SQLi" in unp and "Legacy item" in unp
    # stats line reports the split
    assert "1 confirmed" in out and "2 unproven" in out


def test_render_report_empty_tiers(tmp_path, monkeypatch):
    from reverser.tools.kb import _render_report
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb; reverser.kb._kb_cache.clear()
    from reverser.kb.store import KB
    out = _render_report(KB("empty"))
    assert "## Confirmed Findings" in out and "## Unproven / Needs Verification" in out
    assert out.count("_None._") >= 2
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_report_tiers.py -k render_report -v` → FAIL (no `## Confirmed Findings` / stats split yet).

- [ ] **Step 3: Implement** — in `kb.py`:

(a) Update the stats line. Replace `f"{len(findings)} finding(s), {len(artifacts)} artifact(s).",` with a version that computes the split. Just before the `lines += ["## Engagement Statistics", ...]` block, add:
```python
    _confirmed = [f for f in findings if _is_confirmed(f)]
    _unproven = [f for f in findings if not _is_confirmed(f)]
```
and change the stats sentence to:
```python
        f"{len(findings)} finding(s) "
        f"({len(_confirmed)} confirmed, {len(_unproven)} unproven), "
        f"{len(artifacts)} artifact(s).",
```

(b) Replace the flat `## Findings` block (the `lines.append("## Findings")` … `lines.append("")` section, kb.py ~765-782) with two tiers. Add a small render helper above `_render_report`:
```python
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def _render_finding_block(lines, f, marker: str) -> None:
    reach = f.reachability or "unknown"
    lines.append(f"### [{f.severity.upper()}] {f.title}  {marker} {reach}")
    if f.cvss is not None:
        lines.append(f"_CVSS: {f.cvss}_")
    lines.append("")
    lines.append(f.description or "_(no description)_")
    if f.evidence_paths:
        lines.append("")
        lines.append("**Evidence:**")
        for p in f.evidence_paths:
            lines.append(f"- `{p}`")
    lines.append("")


def _finding_sort_key(f):
    try:
        sev = _SEVERITY_ORDER.index((f.severity or "info").lower())
    except ValueError:
        sev = len(_SEVERITY_ORDER)
    return (sev, (f.title or "").lower())
```
Then the tiered section (replacing the old `## Findings` block):
```python
    lines.append("## Confirmed Findings")
    lines.append("")
    if _confirmed:
        for f in sorted(_confirmed, key=_finding_sort_key):
            _render_finding_block(lines, f, "✓")  # check mark
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Unproven / Needs Verification")
    lines.append("")
    lines.append("_These findings are not yet proven — reachability is unconfirmed "
                 "or evidence is missing. Verify before relying on them._")
    lines.append("")
    if _unproven:
        for f in sorted(_unproven, key=_finding_sort_key):
            suffix = f
            reach = f.reachability or "unknown"
            mark = "⚠"  # warning sign
            # annotate unvalidated explicitly
            lines.append(
                f"### [{f.severity.upper()}] {f.title}  {mark} {reach}"
                + ("" if getattr(f, "validated", True) else " (unvalidated)"))
            if f.cvss is not None:
                lines.append(f"_CVSS: {f.cvss}_")
            lines.append("")
            lines.append(f.description or "_(no description)_")
            if f.evidence_paths:
                lines.append("")
                lines.append("**Evidence:**")
                for p in f.evidence_paths:
                    lines.append(f"- `{p}`")
            lines.append("")
    else:
        lines.append("_None._")
    lines.append("")
```

(Use the `_render_finding_block` helper for the Confirmed tier; the Unproven tier inlines the same shape plus the `(unvalidated)` suffix and ⚠ marker. Keep `✓` / `⚠` as escapes to avoid encoding surprises.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_report_tiers.py -v` → PASS. Then regression: `pytest tests/test_kb_tools.py tests/test_kb_hypotheses.py -q` → all pass (the report-export tests that assert on the old "## Findings" heading, if any, must be updated to the new headings — check `test_kb_tools.py` for `## Findings` / `_No findings recorded._` assertions and migrate them to `## Confirmed Findings` / `## Unproven` / `_None._`).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_report_tiers.py
# include any kb_tools report-assertion test you migrated:
# git add tests/test_kb_tools.py
git commit -m "feat(kb): tier report into Confirmed vs Unproven findings"
```

---

## Task 5: full regression

- [ ] **Step 1:** `mkdir -p logs` (worktree), then `PYTHONPATH="$PWD/src" .venv-python -m pytest -q` → all green (≤2 skipped). Most likely failures: an existing report-export test asserting the old `## Findings` heading or `_No findings recorded._` text → migrate to the new headings/`_None._`. The dispatch reconciliation tests must still pass (backstop findings use blocker+unknown → land in Unproven, not rejected).
- [ ] **Step 2:** `PYTHONPATH="$PWD/src" .venv-python -c "from reverser.tools.kb import _is_confirmed, _render_report; from reverser.schemas.models import FindingModel; print('ok')"` → no import error.
- [ ] **Step 3:** commit any cleanup.

---

## Self-Review notes

- **Spec coverage:** write-time demonstrated reject (Task 1) + tool surface (Task 2); classifier (Task 3); tiered report + stats split (Task 4); regression (Task 5). All spec sections map.
- **Enum vs string:** Task 1 compares `self.reachability == Reachability.demonstrated` (enum, model side); Tasks 3-4 compare `f.reachability == "demonstrated"` (string, FindingFact/report side). Consistent with the spec.
- **No silent drops:** every finding renders in exactly one tier; critical-severity theoretical findings appear in Unproven with severity shown.
- **Confirm-during-TDD:** the real `kb_add_finding` test fixture/auth/handler names in `tests/test_kb_tools.py`; whether any existing report test asserts the old `## Findings` heading (migrate it); that dispatch reconcile tests stay green.
- **Type consistency:** `_is_confirmed(f) -> bool`, `_render_finding_block(lines, f, marker)`, `_finding_sort_key(f)`, `_SEVERITY_ORDER` used consistently across Tasks 3-4 and tests.
```
