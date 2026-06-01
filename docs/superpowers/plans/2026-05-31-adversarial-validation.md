# Adversarial Second-Model Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Before a hypothesis is promoted to `confirmed`, run a read-only second-model adversary that tries to refute it from the KB evidence; a `refuted` verdict hard-blocks the confirm, every verdict is recorded, and the whole gate is opt-in (skipped when no validator model is configured).

**Architecture:** Three Optional `validation_*` fields on `SessionConfig` (opt-in); a new `src/reverser/adversary.py` (`run_adversary_validation` → `Verdict`; a one-shot read-only sub-agent on the configured model with robust JSON/markdown verdict parsing); and a gate inside `kb_update_hypothesis` that runs the adversary on the `confirmed` transition, blocks on `refuted`, records the verdict, and fails open.

**Tech Stack:** Python 3.11+, `create_backend` + `AgentEvent`, SQLite KB, pytest/pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-05-31-adversarial-validation-design.md](../specs/2026-05-31-adversarial-validation-design.md)

**Test command:** `PYTHONPATH="$PWD/src" /Users/jrizzo/Projects/gitea/johnrizzo1/reverser/.venv/bin/python -m pytest <args>`

---

## File Structure

- **Create** `src/reverser/adversary.py` — `Verdict`, `parse_verdict(text)`, `run_adversary_validation(...)`.
- **Modify** `src/reverser/sessions.py` — `SessionConfig` gains `validation_backend/model/api_base` (Optional, default None).
- **Modify** `src/reverser/tools/kb.py` — `kb_update_hypothesis` confirm-gate + `_serialize_evidence_for_validation`.
- **Modify** `src/reverser/cli.py` + session-construction path — `--validation-backend/-model/-api-base` flags → SessionConfig.
- **Tests** `tests/test_adversary.py`, `tests/test_hypothesis_validation_gate.py`, extend `tests/test_session_resume.py`, CLI test.

**Verified facts (read on current `main`):**
- `create_backend(name, tools, *, model=None, api_base=None, model_family=None) -> Backend`; `backend.run(prompt, system_prompt, *, max_turns=50, max_budget_usd=5.0, allowed_tools=None)` is an async generator of `AgentEvent` (kinds incl. `text`, `result`; fields `content`, `cost`, `turns`). `ALL_TOOLS` from `reverser.tools`.
- `SessionConfig` (sessions.py) fields: `profile, backend, model, api_base, budget, max_turns, max_parallel`; serialized via `asdict`, loaded via `SessionConfig(**config_data)` → new Optional fields persist + are backward-compatible.
- `kb_update_hypothesis` (tools/kb.py ~497-530): after `validate_args(HypothesisUpdateModel, …)` and the `if not outcome.ok: return …` check, it builds `update_kwargs = {k: args[k] for k in (...,"evidence_refs",...) if k in args}` then `kb.update_hypothesis(args["id"], **update_kwargs)`. `current = kb.get_hypothesis(args["id"])` and `new_status = args.get("status", current.status)` are already in scope. The handler is `async`.
- `KB.resolve_evidence_refs(refs) -> [{"kind","id","data"}]` (data is the row dict; unknown kinds / missing rows dropped). `KB.record_note(body)` exists. `current_session` import: `from ..sessions import current_session`; `current_session.get()`.

**Import-cycle note:** `tools/__init__` aggregates `ALL_TOOLS` from tool modules incl. `kb`, and `kb.py` will import `run_adversary_validation` from `adversary`. To avoid a load cycle (`tools` → `kb` → `adversary` → `tools`), `adversary.py` imports `ALL_TOOLS` **lazily inside** `run_adversary_validation` (function-local), NOT at module top. Task 1 + Task 5 verify the import is clean.

---

## Task 1: adversary module (`adversary.py`)

**Files:** Create `src/reverser/adversary.py`; create `tests/test_adversary.py`.

- [ ] **Step 1: Write the failing test** — create `tests/test_adversary.py`:

```python
import pytest

from reverser.adversary import Verdict, parse_verdict, run_adversary_validation


def test_parse_json_block():
    txt = 'prose\n```json\n{"verdict": "refuted", "reasoning": "no link to RCE"}\n```'
    v = parse_verdict(txt)
    assert v.verdict == "refuted" and "RCE" in v.reasoning


def test_parse_markdown_fallback():
    v = parse_verdict("VERDICT: upheld\nREASONING: evidence holds")
    assert v.verdict == "upheld" and "holds" in v.reasoning


def test_parse_unparseable_is_inconclusive():
    assert parse_verdict("rambling with no verdict").verdict == "inconclusive"


def test_parse_normalizes_unknown_verdict_to_inconclusive():
    assert parse_verdict('```json\n{"verdict": "maybe", "reasoning": "x"}\n```').verdict == "inconclusive"


class _FakeBackend:
    def __init__(self, *a, **k):
        self.kwargs = None
    async def run(self, prompt, system_prompt, *, max_turns=50, max_budget_usd=5.0, allowed_tools=None):
        self.kwargs = {"allowed_tools": allowed_tools, "max_turns": max_turns}
        from reverser.backends.base import AgentEvent
        yield AgentEvent(kind="text", content='```json\n{"verdict":"refuted","reasoning":"gap"}\n```')
        yield AgentEvent(kind="result", subtype="success", cost=0.02, turns=1)


@pytest.mark.asyncio
async def test_run_adversary_is_read_only_and_parses(monkeypatch):
    import reverser.adversary as adv
    fake = _FakeBackend()
    monkeypatch.setattr(adv, "create_backend", lambda *a, **k: fake)
    v = await run_adversary_validation(
        "DC allows unsigned SMB", "evidence: nmap signing:off",
        backend_name="claude", model=None, api_base=None,
    )
    assert v.verdict == "refuted" and v.cost == 0.02
    assert fake.kwargs["allowed_tools"] == []   # read-only: no tools
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_adversary.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — create `src/reverser/adversary.py`:

```python
"""Adversarial second-model validation: a one-shot, read-only skeptic that tries
to refute a claim using only the provided evidence."""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from typing import Optional

from .backends import create_backend

_VERDICTS = {"refuted", "upheld", "inconclusive"}
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)
_MD_VERDICT = re.compile(r"verdict\s*[:=]\s*([a-z]+)", re.IGNORECASE)
_MD_REASON = re.compile(r"reasoning\s*[:=]\s*(.+)", re.IGNORECASE)

_SYSTEM = (
    "You are a skeptical security reviewer. Your job is to REFUTE the claim below "
    "using ONLY the evidence provided — look for missing links, alternative "
    "explanations, and unproven assumptions. If you genuinely cannot refute it, say "
    "so. Respond with a fenced ```json block: "
    '{"verdict": "refuted|upheld|inconclusive", "reasoning": "<one sentence>"}. '
    "verdict='refuted' means the evidence does NOT support the claim; 'upheld' "
    "means you could not refute it; 'inconclusive' means you cannot tell."
)


@dataclass
class Verdict:
    verdict: str  # "refuted" | "upheld" | "inconclusive"
    reasoning: str = ""
    model: Optional[str] = None
    cost: float = 0.0
    turns: int = 0


def parse_verdict(text: str, *, model: Optional[str] = None) -> Verdict:
    """Parse a verdict. JSON block preferred, then markdown VERDICT:/REASONING:;
    unparseable or unknown verdict -> inconclusive."""
    text = text or ""
    obj = None
    for pat in (_JSON_BLOCK, _BARE_JSON):
        for m in pat.finditer(text):
            try:
                cand = _json.loads(m.group(1))
            except (ValueError, TypeError):
                continue
            if isinstance(cand, dict) and "verdict" in cand:
                obj = cand
                break
        if obj is not None:
            break
    if obj is not None:
        verdict = str(obj.get("verdict", "")).strip().lower()
        reasoning = str(obj.get("reasoning", "")).strip()
    else:
        mv = _MD_VERDICT.search(text)
        verdict = mv.group(1).strip().lower() if mv else ""
        mr = _MD_REASON.search(text)
        reasoning = mr.group(1).strip() if mr else ""
    if verdict not in _VERDICTS:
        verdict = "inconclusive"
    return Verdict(verdict=verdict, reasoning=reasoning, model=model)


async def run_adversary_validation(
    claim: str,
    evidence_text: str,
    *,
    backend_name: str,
    model: Optional[str],
    api_base: Optional[str],
    max_turns: int = 3,
    budget_usd: float = 0.10,
) -> Verdict:
    """Run a one-shot READ-ONLY adversary (no tool calls) to try to refute `claim`
    using `evidence_text`. Returns a parsed Verdict (cost/turns filled in)."""
    # Lazy import to avoid a module-load cycle (tools -> kb -> adversary -> tools).
    from .tools import ALL_TOOLS

    backend = create_backend(backend_name, ALL_TOOLS, model=model, api_base=api_base)
    prompt = (
        f"CLAIM (a hypothesis someone wants to mark CONFIRMED):\n{claim}\n\n"
        f"EVIDENCE AVAILABLE:\n{evidence_text}\n\n"
        "Try to refute the claim using only this evidence, then give your verdict."
    )
    report = ""
    cost = 0.0
    turns = 0
    async for ev in backend.run(
        prompt=prompt, system_prompt=_SYSTEM,
        max_turns=max_turns, max_budget_usd=budget_usd, allowed_tools=[],
    ):
        if ev.kind == "text" and ev.content:
            report = ev.content
        elif ev.kind == "result":
            cost = float(ev.cost or 0.0)
            turns = int(ev.turns or 0)
    v = parse_verdict(report, model=model)
    v.cost = cost
    v.turns = turns
    return v
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_adversary.py -v` → PASS (5). Also: `python -c "import reverser.adversary; from reverser.tools import ALL_TOOLS; print('ok')"` (no cycle).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/adversary.py tests/test_adversary.py
git commit -m "feat(adversary): read-only second-model validation (run + verdict parse)"
```

---

## Task 2: SessionConfig validation fields

**Files:** Modify `src/reverser/sessions.py`; extend `tests/test_session_resume.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_session_resume.py`:

```python
def test_session_config_validation_fields_round_trip():
    from reverser.sessions import SessionConfig
    from dataclasses import asdict
    c = SessionConfig(profile="ad", validation_backend="claude",
                      validation_model="m", validation_api_base="http://x/v1")
    c2 = SessionConfig(**asdict(c))
    assert c2.validation_backend == "claude" and c2.validation_model == "m"
    assert c2.validation_api_base == "http://x/v1"


def test_session_config_validation_defaults_none():
    from reverser.sessions import SessionConfig
    assert SessionConfig(profile="general").validation_backend is None
    # old snapshot (no validation_* keys) still loads
    c2 = SessionConfig(profile="general", backend="claude", model=None, api_base=None,
                       budget=5.0, max_turns=50, max_parallel=1)
    assert c2.validation_backend is None
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_session_resume.py -k validation_fields -v` → FAIL (`unexpected keyword argument 'validation_backend'`).

- [ ] **Step 3: Implement** — in `sessions.py` `SessionConfig`, insert after the `api_base` field, before `budget`:

```python
    # Adversarial hypothesis validation (opt-in). None = no adversary runs.
    validation_backend: Optional[str] = None
    validation_model: Optional[str] = None
    validation_api_base: Optional[str] = None
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_session_resume.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/sessions.py tests/test_session_resume.py
git commit -m "feat(sessions): SessionConfig validation_backend/model/api_base (opt-in)"
```

---

## Task 3: confirm-gate in kb_update_hypothesis

**Files:** Modify `src/reverser/tools/kb.py`; create `tests/test_hypothesis_validation_gate.py`.

- [ ] **Step 1: Write the failing test** — create `tests/test_hypothesis_validation_gate.py`:

```python
import pytest
from types import SimpleNamespace

import reverser.tools.kb as kbmod
from reverser.tools.kb import kb_add_hypothesis, kb_update_hypothesis
from reverser.adversary import Verdict


def _handler(tool):
    return getattr(tool, "handler", None) or getattr(tool, "fn", None) or tool


def _session(monkeypatch, *, validation_backend=None):
    from reverser.sessions import current_session
    sess = SimpleNamespace(config=SimpleNamespace(
        validation_backend=validation_backend, validation_model="adv",
        validation_api_base=None))
    monkeypatch.setattr(current_session, "get", lambda: sess)
    return sess


async def _confirmable_hyp(target):
    add = await _handler(kb_add_hypothesis)({
        "target": target, "statement": "DC allows unsigned SMB",
        "rationale": "nmap", "confidence": 60})
    hid = int(add["content"][0]["text"].split("#")[1].split(" ")[0])
    await _handler(kb_update_hypothesis)({"target": target, "id": hid, "status": "testing"})
    return hid


@pytest.fixture(autouse=True)
def _authz(monkeypatch):
    monkeypatch.setattr(kbmod, "_check_auth", lambda: None)


@pytest.mark.asyncio
async def test_refuted_blocks_confirm(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    _session(monkeypatch, validation_backend="claude")
    async def fake_adv(*a, **k):
        return Verdict(verdict="refuted", reasoning="no SMB signing evidence", model="adv")
    monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
    hid = await _confirmable_hyp("t1")
    res = await _handler(kb_update_hypothesis)({
        "target": "t1", "id": hid, "status": "confirmed",
        "evidence_refs": [{"kind": "note", "id": 1}]})
    assert res.get("is_error") is True
    assert "refus" in res["content"][0]["text"].lower()
    from reverser.kb import for_target
    assert for_target("t1").get_hypothesis(hid).status == "testing"  # NOT confirmed


@pytest.mark.asyncio
async def test_upheld_allows_confirm(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    _session(monkeypatch, validation_backend="claude")
    async def fake_adv(*a, **k):
        return Verdict(verdict="upheld", reasoning="evidence holds", model="adv")
    monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
    hid = await _confirmable_hyp("t2")
    res = await _handler(kb_update_hypothesis)({
        "target": "t2", "id": hid, "status": "confirmed",
        "evidence_refs": [{"kind": "note", "id": 1}]})
    assert res.get("is_error") is not True
    from reverser.kb import for_target
    assert for_target("t2").get_hypothesis(hid).status == "confirmed"


@pytest.mark.asyncio
async def test_no_validator_skips_adversary(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    _session(monkeypatch, validation_backend=None)  # OFF
    called = {"n": 0}
    async def fake_adv(*a, **k):
        called["n"] += 1
        return Verdict(verdict="refuted", reasoning="x")
    monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
    hid = await _confirmable_hyp("t3")
    res = await _handler(kb_update_hypothesis)({
        "target": "t3", "id": hid, "status": "confirmed",
        "evidence_refs": [{"kind": "note", "id": 1}]})
    assert res.get("is_error") is not True and called["n"] == 0
    from reverser.kb import for_target
    assert for_target("t3").get_hypothesis(hid).status == "confirmed"


@pytest.mark.asyncio
async def test_adversary_error_fails_open(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    _session(monkeypatch, validation_backend="claude")
    async def boom(*a, **k):
        raise RuntimeError("validator down")
    monkeypatch.setattr(kbmod, "run_adversary_validation", boom)
    hid = await _confirmable_hyp("t4")
    res = await _handler(kb_update_hypothesis)({
        "target": "t4", "id": hid, "status": "confirmed",
        "evidence_refs": [{"kind": "note", "id": 1}]})
    assert res.get("is_error") is not True  # fail-open
    from reverser.kb import for_target
    assert for_target("t4").get_hypothesis(hid).status == "confirmed"


@pytest.mark.asyncio
async def test_non_confirmed_transition_skips_adversary(tmp_targets_dir, monkeypatch):
    import reverser.kb
    reverser.kb._kb_cache.clear()
    _session(monkeypatch, validation_backend="claude")
    called = {"n": 0}
    async def fake_adv(*a, **k):
        called["n"] += 1
        return Verdict(verdict="refuted", reasoning="x")
    monkeypatch.setattr(kbmod, "run_adversary_validation", fake_adv)
    hid = await _confirmable_hyp("t5")
    res = await _handler(kb_update_hypothesis)({
        "target": "t5", "id": hid, "status": "abandoned", "rationale": "drop"})
    assert res.get("is_error") is not True and called["n"] == 0
```

(`tmp_targets_dir` + `_check_auth` patch + the `_handler` shim mirror `tests/test_kb_hypotheses.py`/`test_kb_tools.py`. Confirm those exact names when writing and adapt if different.)

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_hypothesis_validation_gate.py -v` → FAIL (refuted does not block).

- [ ] **Step 3: Implement** — in `tools/kb.py`:

(a) Add the import next to the other schema imports (near `from ..schemas.validation import validate_args, tool_input_schema`):
```python
from ..adversary import run_adversary_validation
```

(b) Add the evidence-serialization helper at module scope (e.g. just above `kb_update_hypothesis`):
```python
def _serialize_evidence_for_validation(kb, hypothesis, evidence_refs) -> str:
    """Compact text of a hypothesis + its dereferenced evidence for the adversary."""
    lines = [f"Hypothesis: {getattr(hypothesis, 'statement', '')}"]
    if getattr(hypothesis, "rationale", None):
        lines.append(f"Rationale: {hypothesis.rationale}")
    refs = list(evidence_refs or [])
    if getattr(hypothesis, "evidence_refs", None):
        refs = refs + list(hypothesis.evidence_refs)
    seen = set()
    for item in kb.resolve_evidence_refs(refs):
        key = (item["kind"], item["id"])
        if key in seen:
            continue
        seen.add(key)
        data = item.get("data") or {}
        if item["kind"] == "finding":
            lines.append(f"- finding: {str(data.get('title', ''))[:120]} "
                         f"[{data.get('severity', '')}] {str(data.get('description', ''))[:300]}")
        elif item["kind"] == "note":
            body = data.get("body") if isinstance(data, dict) else str(data)
            lines.append(f"- note: {str(body)[:300]}")
        elif item["kind"] == "credential":
            lines.append(f"- cred: {data.get('username', '')}@{data.get('domain', '') or '-'} "
                         f"({data.get('status', '')})")
        elif item["kind"] == "service":
            lines.append(f"- service: {data.get('host_ip', '')}:{data.get('port', '')} "
                         f"{data.get('service', '')}")
    return "\n".join(lines)[:4000]
```

(c) Insert the gate in `kb_update_hypothesis` BETWEEN the `if not outcome.ok: return format_error(outcome.error_text)` line and the `update_kwargs = {...}` line. Because the success path builds `update_kwargs` from `args` keys, the gate appends its validation marker by mutating `args["evidence_refs"]` (so the existing dict-comprehension picks it up — no change to the update call):

```python
    if not outcome.ok:
        return format_error(outcome.error_text)

    # ── Adversarial validation gate (opt-in) ─────────────────────────
    # Before promoting to 'confirmed', a read-only second-model skeptic tries to
    # REFUTE the hypothesis from the KB evidence. 'refuted' hard-blocks; otherwise
    # the verdict is recorded and we proceed. Skipped if no validator configured;
    # fails open on adversary error.
    if new_status == "confirmed":
        try:
            from ..sessions import current_session
            sess = current_session.get()
        except Exception:
            sess = None
        vbackend = getattr(getattr(sess, "config", None), "validation_backend", None)
        if vbackend:
            vmodel = getattr(sess.config, "validation_model", None)
            vapi = getattr(sess.config, "validation_api_base", None)
            evidence_text = _serialize_evidence_for_validation(
                kb, current, args.get("evidence_refs"))
            try:
                verdict = await run_adversary_validation(
                    claim=current.statement, evidence_text=evidence_text,
                    backend_name=vbackend, model=vmodel, api_base=vapi)
            except Exception as e:
                kb.record_note(
                    f"Adversarial validation unavailable for hyp #{args['id']} "
                    f"({type(e).__name__}: {e}); confirmed without it.")
                verdict = None
            if verdict is not None and verdict.verdict == "refuted":
                kb.record_note(
                    f"Adversarial validation REFUTED hyp #{args['id']} "
                    f"(model={vmodel}): {verdict.reasoning}")
                return format_error(
                    "Adversarial validation refused the 'confirmed' transition: "
                    f"{verdict.reasoning}. Revise the hypothesis/evidence, gather more, "
                    "or use status='testing'/'inconclusive'.")
            if verdict is not None:
                kb.record_note(
                    f"Adversarial validation {verdict.verdict} hyp #{args['id']} "
                    f"(model={vmodel}): {verdict.reasoning}")
                args["evidence_refs"] = list(args.get("evidence_refs") or []) + [{
                    "kind": "validation", "verdict": verdict.verdict,
                    "model": vmodel, "reasoning": verdict.reasoning}]

    update_kwargs = {
        k: args[k]
        for k in ("status", "rationale", "confidence", "dispatched_to",
                  "evidence_refs", "tags")
        if k in args
    }
    kb.update_hypothesis(args["id"], **update_kwargs)
```

(Leave the rest of the handler — emit + success message — unchanged.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_hypothesis_validation_gate.py -v` → PASS (5). Then regression: `pytest tests/test_kb_hypotheses.py tests/test_kb_tools.py tests/test_tool_registry.py -q` → all pass (existing confirm tests have no session/validator configured, so the gate is skipped).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_hypothesis_validation_gate.py
git commit -m "feat(kb): adversarial validation gate on hypothesis confirm transition"
```

---

## Task 4: CLI flags → SessionConfig

**Files:** Modify `src/reverser/cli.py` (+ the session-construction seam it uses); add/extend a CLI test.

- [ ] **Step 1: Write the failing test** — first READ `cli.py` to find the interactive (`i`) subparser and how it builds the session (look for `add_argument("--backend"...)` and where `SessionConfig`/`AgentSession` is constructed from `args.backend`/`args.model`). Add a test in the file that already tests CLI parsing/sessions (`tests/test_cli.py` or `tests/test_cli_sessions.py`) asserting the new flags parse and thread through. Target the REAL seam — e.g. if there's a parser factory:

```python
def test_cli_parses_validation_flags():
    from reverser.cli import build_parser  # use the real factory name found in cli.py
    args = build_parser().parse_args(
        ["i", "10.0.0.1", "--validation-backend", "claude", "--validation-model", "advm"])
    assert args.validation_backend == "claude" and args.validation_model == "advm"
```

If cli.py has no parser factory, test the smallest real function that maps parsed args → `SessionConfig` (read cli.py; do NOT invent a function name).

- [ ] **Step 2: Run to verify it fails** — `unrecognized arguments` / attribute error.

- [ ] **Step 3: Implement** — add to the interactive subparser in `cli.py` (next to the existing `--backend`/`--model`/`--api-base` args):

```python
    interactive_parser.add_argument("--validation-backend", default=None,
        help="Backend for adversarial hypothesis validation (e.g. claude, ollama). "
             "If unset, no adversarial validation runs.")
    interactive_parser.add_argument("--validation-model", default=None,
        help="Model for the validation backend (ideally different from the main model).")
    interactive_parser.add_argument("--validation-api-base", default=None,
        help="API base for the validation backend.")
```

Then thread `args.validation_backend/model/api_base` into the `SessionConfig` the interactive command builds — follow the exact path the existing `backend`/`model`/`api_base` take (whether that's `AgentSession(...)`, `AgentSession.from_target(...)`, or a `session_start` helper). If the constructor doesn't accept them, add matching keyword params that flow into the `SessionConfig` it builds, mirroring `backend_name`/`model`/`api_base`.

- [ ] **Step 4: Run to verify it passes** — the new test + `pytest tests/test_cli.py tests/test_cli_sessions.py -q` → pass.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/cli.py src/reverser/agent_session.py tests/test_cli*.py
git commit -m "feat(cli): --validation-backend/-model/-api-base -> SessionConfig"
```
(Only `git add` the files you actually changed.)

---

## Task 5: Full regression

- [ ] **Step 1:** ensure `logs/` exists in the worktree (`mkdir -p logs`), then `pytest -q` → all green (≤2 skipped).
- [ ] **Step 2:** `python -c "from reverser.adversary import run_adversary_validation; from reverser.tools import ALL_TOOLS; import reverser.tools.kb; print('ok', len(ALL_TOOLS))"` → no import error / cycle.
- [ ] **Step 3:** commit any cleanup.

---

## Self-Review notes

- **Spec coverage:** adversary module + read-only + verdict parse (Task 1); opt-in config + persistence (Task 2); confirm-gate block/record/skip/fail-open + evidence serialization (Task 3); CLI plumbing (Task 4); regression (Task 5). All spec sections map.
- **Gate insertion point matches real code:** between the `if not outcome.ok` return and the `update_kwargs` dict-comprehension; the marker is appended by mutating `args["evidence_refs"]` so the existing comprehension + `kb.update_hypothesis(**update_kwargs)` call are unchanged.
- **Import cycle:** `adversary.py` imports `ALL_TOOLS` lazily inside the function (verified in Tasks 1 & 5).
- **Confirm-during-TDD:** real kb-tool test fixture/auth names (`tmp_targets_dir`, `_check_auth`, handler shim); the real cli.py parser/construction seam for Task 4; existing confirm tests stay green with the gate skipped (no session configured).
- **Type consistency:** `Verdict(verdict, reasoning, model, cost, turns)`; `run_adversary_validation(claim, evidence_text, *, backend_name, model, api_base, max_turns, budget_usd)`; verdicts `refuted|upheld|inconclusive` consistent across module/gate/tests.
```
