# Schema-Validated Structured Outputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put reverser's agent outputs (findings, hypotheses, final report, specialist dispatch reports) under validated Pydantic contracts with reject-and-retry enforcement.

**Architecture:** A new `src/reverser/schemas/` package holds Pydantic v2 models (single source of truth) plus `validate_args` / `tool_input_schema` helpers. Enforcement at two points: (A) tool boundary — `kb_*` handlers reject invalid args with an actionable `is_error` result so the agent self-corrects; (B) bounded emit+repair inside `dispatch_specialist` for the specialist return contract. No backend-loop changes.

**Tech Stack:** Python 3.11+, Pydantic 2.13.4 (already installed via FastAPI), `claude-agent-sdk` `@tool` decorator, SQLite, pytest / pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-05-30-schema-validated-outputs-design.md](../specs/2026-05-30-schema-validated-outputs-design.md)

---

## File Structure

- **Create** `src/reverser/schemas/__init__.py` — package exports (models + helpers).
- **Create** `src/reverser/schemas/models.py` — `Severity`, `Reachability`, `FindingModel`, `HypothesisModel`, `HypothesisUpdateModel`, `DispatchReportModel`, `ReportModel`.
- **Create** `src/reverser/schemas/validation.py` — `ValidationOutcome`, `validate_args`, `render_errors`, `tool_input_schema`.
- **Modify** `src/reverser/kb/schema.py` — additive findings columns + schema version bump 2→3 + migration.
- **Modify** `src/reverser/kb/store.py` — `record_finding` persists new finding fields; `get_findings` tolerates legacy rows; `FindingFact` gains optional fields.
- **Modify** `src/reverser/tools/kb.py` — `kb_add_finding`, `kb_add_hypothesis`, `kb_update_hypothesis`, `kb_export_report` use the models.
- **Modify** `src/reverser/tools/dispatch.py` — replace regex parsing with `DispatchReportModel`; add bounded emit+repair.
- **Create** `tests/schemas/__init__.py`, `tests/schemas/test_models.py`, `tests/schemas/test_validation.py`, `tests/schemas/test_tool_input_schema.py`.
- **Modify** `tests/test_kb_tools.py`, `tests/test_kb_hypotheses.py`, `tests/test_kb_schema.py`, `tests/test_dispatch.py`, `tests/test_dispatch_helpers.py` — integration coverage.

**Design refinements locked in here (consistent with spec):**
- The hypothesis "blocker escape" is the existing `status='blocked'` state (already in the DDL CHECK and the `kb_update_hypothesis` enum), reachable from any non-terminal state and requiring a `rationale`. No separate field.
- Finding "blocker escape" is a new `evidence_blocker` column; when set, the finding is stored with `validated=0` and `reachability` clamped to ≤ `theoretical`.

---

## Task 1: schemas package + FindingModel

**Files:**
- Create: `src/reverser/schemas/__init__.py`
- Create: `src/reverser/schemas/models.py`
- Create: `tests/schemas/__init__.py`
- Test: `tests/schemas/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/schemas/__init__.py` (empty file) and `tests/schemas/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from reverser.schemas.models import FindingModel, Severity, Reachability


def _valid_finding_kwargs():
    return dict(
        title="SQL injection in /login",
        severity="high",
        description="The id param is concatenated into a query.",
        evidence_paths=["findings/sqli.txt"],
        reproduction="POST /login with id=1' OR '1'='1",
        confidence=80,
        reachability="demonstrated",
    )


def test_valid_finding_passes():
    m = FindingModel(**_valid_finding_kwargs())
    assert m.severity == Severity.high
    assert m.reachability == Reachability.demonstrated
    assert m.validated is True


def test_finding_requires_evidence():
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    with pytest.raises(ValidationError):
        FindingModel(**kw)


def test_finding_requires_reproduction():
    kw = _valid_finding_kwargs()
    del kw["reproduction"]
    with pytest.raises(ValidationError):
        FindingModel(**kw)


def test_finding_confidence_range():
    kw = _valid_finding_kwargs()
    kw["confidence"] = 150
    with pytest.raises(ValidationError):
        FindingModel(**kw)


def test_finding_bad_severity():
    kw = _valid_finding_kwargs()
    kw["severity"] = "spicy"
    with pytest.raises(ValidationError):
        FindingModel(**kw)


def test_evidence_blocker_allows_empty_evidence_and_marks_degraded():
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    kw["reachability"] = "demonstrated"
    kw["evidence_blocker"] = "Target offline; could not capture PoC output."
    m = FindingModel(**kw)
    assert m.validated is False
    # reachability clamped to <= theoretical
    assert m.reachability == Reachability.theoretical
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schemas/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.schemas'`

- [ ] **Step 3: Write minimal implementation**

Create `src/reverser/schemas/models.py`:

```python
"""Pydantic v2 models — single source of truth for validated agent outputs."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Reachability(str, Enum):
    demonstrated = "demonstrated"
    likely = "likely"
    theoretical = "theoretical"
    unknown = "unknown"


_REACHABILITY_ORDER = {
    Reachability.unknown: 0,
    Reachability.theoretical: 1,
    Reachability.likely: 2,
    Reachability.demonstrated: 3,
}


class FindingModel(BaseModel):
    """A security finding recorded into the KB."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    title: str = Field(min_length=1, max_length=120, description="Short finding title.")
    severity: Severity = Field(description="Severity level.")
    description: str = Field(min_length=1, description="Finding details.")
    evidence_paths: list[str] = Field(
        default_factory=list,
        description="Evidence file paths (relative to the target dir). "
        "At least one is required unless evidence_blocker is set.",
    )
    reproduction: str = Field(
        min_length=1, description="How to reproduce / trigger the finding."
    )
    confidence: int = Field(ge=0, le=100, description="Confidence, 0-100.")
    reachability: Reachability = Field(
        description="demonstrated|likely|theoretical|unknown."
    )
    cvss: float | None = Field(default=None, ge=0.0, le=10.0, description="Optional CVSS 0-10.")
    evidence_blocker: str | None = Field(
        default=None,
        description="If you cannot supply evidence_paths, explain why here. "
        "Setting this stores the finding flagged as unvalidated and clamps "
        "reachability to at most 'theoretical'.",
    )
    validated: bool = Field(default=True, description="Internal: False when degraded via blocker.")

    @model_validator(mode="after")
    def _check_evidence_or_blocker(self) -> "FindingModel":
        non_empty = [p for p in self.evidence_paths if p and p.strip()]
        self.evidence_paths = non_empty
        if not non_empty:
            if not (self.evidence_blocker and self.evidence_blocker.strip()):
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

Create `src/reverser/schemas/__init__.py`:

```python
"""Validated output schemas for reverser agent outputs."""

from .models import (
    FindingModel,
    HypothesisModel,
    HypothesisUpdateModel,
    DispatchReportModel,
    ReportModel,
    Severity,
    Reachability,
)

__all__ = [
    "FindingModel",
    "HypothesisModel",
    "HypothesisUpdateModel",
    "DispatchReportModel",
    "ReportModel",
    "Severity",
    "Reachability",
]
```

Note: `__init__.py` imports models not yet defined (Tasks 2 & 8). To keep Task 1 runnable, temporarily export only `FindingModel`, `Severity`, `Reachability`, then expand `__all__` and imports in Tasks 2 and 8. For Task 1, use:

```python
"""Validated output schemas for reverser agent outputs."""

from .models import FindingModel, Severity, Reachability

__all__ = ["FindingModel", "Severity", "Reachability"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schemas/test_models.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reverser/schemas/__init__.py src/reverser/schemas/models.py tests/schemas/__init__.py tests/schemas/test_models.py
git commit -m "feat(schemas): add FindingModel with evidence-blocker escape"
```

---

## Task 2: HypothesisModel + HypothesisUpdateModel

**Files:**
- Modify: `src/reverser/schemas/models.py`
- Modify: `src/reverser/schemas/__init__.py`
- Test: `tests/schemas/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/schemas/test_models.py`:

```python
from reverser.schemas.models import HypothesisModel, HypothesisUpdateModel


def test_valid_hypothesis():
    m = HypothesisModel(statement="DC allows unsigned SMB", rationale="nmap showed signing:off", confidence=70)
    assert m.confidence == 70


def test_hypothesis_requires_statement_and_rationale():
    with pytest.raises(ValidationError):
        HypothesisModel(statement="", rationale="x", confidence=10)
    with pytest.raises(ValidationError):
        HypothesisModel(statement="x", rationale="", confidence=10)


@pytest.mark.parametrize("frm,to,ok", [
    ("proposed", "testing", True),
    ("testing", "confirmed", True),
    ("testing", "refuted", True),
    ("testing", "abandoned", True),
    ("proposed", "confirmed", False),   # must pass through testing
    ("confirmed", "testing", False),    # terminal
    ("proposed", "blocked", True),      # blocker escape from any non-terminal
    ("testing", "blocked", True),
])
def test_status_transitions(frm, to, ok):
    kw = dict(from_status=frm, to_status=to, rationale="r", evidence_refs=[{"kind": "finding", "id": 1}])
    if ok:
        HypothesisUpdateModel(**kw)
    else:
        with pytest.raises(ValidationError):
            HypothesisUpdateModel(**kw)


def test_confirmed_requires_evidence():
    with pytest.raises(ValidationError):
        HypothesisUpdateModel(from_status="testing", to_status="confirmed", rationale="r", evidence_refs=[])


def test_blocked_requires_rationale():
    with pytest.raises(ValidationError):
        HypothesisUpdateModel(from_status="testing", to_status="blocked", rationale="", evidence_refs=[])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schemas/test_models.py -k "hypothesis or status or transition or blocked or confirmed" -v`
Expected: FAIL — `ImportError: cannot import name 'HypothesisModel'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/reverser/schemas/models.py`:

```python
class HypothesisStatus(str, Enum):
    proposed = "proposed"
    testing = "testing"
    confirmed = "confirmed"
    refuted = "refuted"
    abandoned = "abandoned"
    blocked = "blocked"


_TERMINAL_STATUSES = {
    HypothesisStatus.confirmed,
    HypothesisStatus.refuted,
    HypothesisStatus.abandoned,
}

# Allowed forward transitions (blocked is reachable from any non-terminal state).
_ALLOWED_TRANSITIONS = {
    HypothesisStatus.proposed: {HypothesisStatus.testing, HypothesisStatus.blocked, HypothesisStatus.abandoned},
    HypothesisStatus.testing: {
        HypothesisStatus.confirmed,
        HypothesisStatus.refuted,
        HypothesisStatus.abandoned,
        HypothesisStatus.blocked,
    },
    HypothesisStatus.blocked: {HypothesisStatus.testing, HypothesisStatus.abandoned},
}

_EVIDENCE_REQUIRED_TARGETS = {HypothesisStatus.confirmed, HypothesisStatus.refuted}


class HypothesisModel(BaseModel):
    """A new hypothesis added to the attack tree."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    statement: str = Field(min_length=1, description="What you are hypothesizing.")
    rationale: str = Field(min_length=1, description="Why you are proposing this.")
    confidence: int = Field(ge=0, le=100, description="Confidence, 0-100.")
    parent_id: int | None = Field(default=None, description="Parent hypothesis id.")
    tags: list[str] = Field(default_factory=list, description="Free-form labels.")


class HypothesisUpdateModel(BaseModel):
    """A status/field update to an existing hypothesis. from_status is the
    current persisted status (supplied by the tool, not the agent)."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    from_status: HypothesisStatus = Field(description="Current persisted status.")
    to_status: HypothesisStatus = Field(description="Requested new status.")
    rationale: str = Field(default="", description="Reason for the change.")
    confidence: int | None = Field(default=None, ge=0, le=100)
    evidence_refs: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_transition(self) -> "HypothesisUpdateModel":
        if self.to_status == self.from_status:
            return self  # no-op status, other fields may change
        if self.from_status in _TERMINAL_STATUSES:
            raise ValueError(
                f"status {self.from_status.value!r} is terminal; cannot transition to "
                f"{self.to_status.value!r}"
            )
        allowed = _ALLOWED_TRANSITIONS.get(self.from_status, set())
        if self.to_status not in allowed:
            raise ValueError(
                f"illegal transition {self.from_status.value!r} -> {self.to_status.value!r}; "
                f"allowed: {sorted(s.value for s in allowed)}"
            )
        if self.to_status in _EVIDENCE_REQUIRED_TARGETS and not self.evidence_refs:
            raise ValueError(
                f"transition to {self.to_status.value!r} requires at least 1 evidence_refs entry"
            )
        if self.to_status == HypothesisStatus.blocked and not (self.rationale and self.rationale.strip()):
            raise ValueError("transition to 'blocked' requires a non-empty rationale")
        return self
```

Update `src/reverser/schemas/__init__.py` to add the new exports:

```python
"""Validated output schemas for reverser agent outputs."""

from .models import (
    FindingModel,
    HypothesisModel,
    HypothesisUpdateModel,
    Severity,
    Reachability,
)

__all__ = [
    "FindingModel",
    "HypothesisModel",
    "HypothesisUpdateModel",
    "Severity",
    "Reachability",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schemas/test_models.py -v`
Expected: PASS (all model tests)

- [ ] **Step 5: Commit**

```bash
git add src/reverser/schemas/models.py src/reverser/schemas/__init__.py tests/schemas/test_models.py
git commit -m "feat(schemas): add Hypothesis create/update models with transition rules"
```

---

## Task 3: validate_args + actionable error rendering

**Files:**
- Create: `src/reverser/schemas/validation.py`
- Test: `tests/schemas/test_validation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/schemas/test_validation.py`:

```python
from reverser.schemas.models import FindingModel
from reverser.schemas.validation import validate_args


def _valid():
    return dict(
        title="t", severity="high", description="d",
        evidence_paths=["findings/x.txt"], reproduction="r",
        confidence=80, reachability="likely",
    )


def test_validate_args_success_returns_model():
    out = validate_args(FindingModel, _valid())
    assert out.ok is True
    assert out.value.title == "t"
    assert out.error_text is None


def test_validate_args_failure_returns_actionable_text():
    bad = _valid()
    bad["confidence"] = 150
    del bad["reproduction"]
    out = validate_args(FindingModel, bad)
    assert out.ok is False
    assert out.value is None
    # one line per error, mentioning the field paths
    assert "confidence" in out.error_text
    assert "reproduction" in out.error_text
    # multiple errors all surface
    assert out.error_text.count("\n") >= 1


def test_validate_args_coerces_stringified_numbers():
    kw = _valid()
    kw["confidence"] = "80"   # OpenAI-compat text path may stringify
    out = validate_args(FindingModel, kw)
    assert out.ok is True
    assert out.value.confidence == 80
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schemas/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reverser.schemas.validation'`

- [ ] **Step 3: Write minimal implementation**

Create `src/reverser/schemas/validation.py`:

```python
"""Validation helpers shared by the KB tools and the dispatch contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel, ValidationError


@dataclass
class ValidationOutcome:
    ok: bool
    value: BaseModel | None
    error_text: str | None


def render_errors(exc: ValidationError) -> str:
    """Render a ValidationError as one actionable line per problem."""
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"✗ {loc}: {err['msg']}")
    header = (
        "Validation failed. Fix the following and resubmit this call "
        "(do not give up — correct the fields):"
    )
    return header + "\n" + "\n".join(lines)


def validate_args(model: Type[BaseModel], args: dict) -> ValidationOutcome:
    """Parse args into the model. On failure return actionable error text."""
    try:
        instance = model(**args)
    except ValidationError as exc:
        return ValidationOutcome(ok=False, value=None, error_text=render_errors(exc))
    return ValidationOutcome(ok=True, value=instance, error_text=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schemas/test_validation.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reverser/schemas/validation.py tests/schemas/test_validation.py
git commit -m "feat(schemas): add validate_args with actionable error rendering"
```

---

## Task 4: tool_input_schema (self-contained @tool schema generator)

**Files:**
- Modify: `src/reverser/schemas/validation.py`
- Test: `tests/schemas/test_tool_input_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/schemas/test_tool_input_schema.py`:

```python
import json

from reverser.schemas.models import FindingModel, HypothesisModel
from reverser.schemas.validation import tool_input_schema


def test_schema_is_self_contained_no_refs():
    schema = tool_input_schema(FindingModel)
    blob = json.dumps(schema)
    assert "$ref" not in blob
    assert "$defs" not in schema and "definitions" not in schema


def test_schema_has_object_shape_and_required():
    schema = tool_input_schema(FindingModel)
    assert schema["type"] == "object"
    assert "properties" in schema
    for f in ("title", "severity", "description", "reproduction", "confidence", "reachability"):
        assert f in schema["required"]


def test_enum_is_inlined():
    schema = tool_input_schema(FindingModel)
    sev = schema["properties"]["severity"]
    assert "enum" in sev
    assert set(sev["enum"]) == {"info", "low", "medium", "high", "critical"}


def test_hypothesis_schema_self_contained():
    schema = tool_input_schema(HypothesisModel)
    assert "$ref" not in json.dumps(schema)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schemas/test_tool_input_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'tool_input_schema'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/reverser/schemas/validation.py`:

```python
import copy


def _inline_refs(node, defs):
    """Recursively replace {"$ref": "#/$defs/X"} with the resolved definition."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            name = ref.split("/")[-1]
            resolved = copy.deepcopy(defs.get(name, {}))
            # merge sibling keys (e.g. description) over the resolved body
            for k, v in node.items():
                if k != "$ref":
                    resolved[k] = v
            return _inline_refs(resolved, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


_INTERNAL_FIELDS = {"validated", "from_status"}


def tool_input_schema(model: Type[BaseModel]) -> dict:
    """Return a self-contained @tool input schema derived from a Pydantic model.

    Inlines all $ref/$defs, drops internal-only fields, and guarantees an
    object schema with a `required` list (claude-agent-sdk @tool shape).
    """
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    inlined = _inline_refs({k: v for k, v in raw.items() if k != "$defs"}, defs)

    props = inlined.get("properties", {})
    for field in _INTERNAL_FIELDS:
        props.pop(field, None)
    required = [r for r in inlined.get("required", []) if r not in _INTERNAL_FIELDS]

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schemas/test_tool_input_schema.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reverser/schemas/validation.py tests/schemas/test_tool_input_schema.py
git commit -m "feat(schemas): add tool_input_schema generator (inlined, self-contained)"
```

---

## Task 5: DB migration — additive finding columns

**Files:**
- Modify: `src/reverser/kb/schema.py`
- Test: `tests/test_kb_schema.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kb_schema.py`:

```python
import sqlite3

from reverser.kb.schema import apply_schema, get_schema_version, SCHEMA_VERSION


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_findings_has_new_columns_on_fresh_db():
    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    cols = _columns(conn, "findings")
    for c in ("reproduction", "reachability", "confidence", "evidence_blocker", "validated"):
        assert c in cols
    assert get_schema_version(conn) == SCHEMA_VERSION


def test_migration_adds_columns_to_legacy_findings_table():
    conn = sqlite3.connect(":memory:")
    # simulate a v2 findings table without the new columns
    conn.execute(
        "CREATE TABLE findings (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id TEXT, "
        "title TEXT NOT NULL, severity TEXT NOT NULL, cvss REAL, description TEXT, "
        "evidence_paths TEXT, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO findings (target_id, title, severity, created_at) "
        "VALUES ('t', 'old', 'low', '2026-01-01T00:00:00')"
    )
    conn.commit()
    apply_schema(conn)
    cols = _columns(conn, "findings")
    for c in ("reproduction", "reachability", "confidence", "evidence_blocker", "validated"):
        assert c in cols
    # legacy row still present and readable
    row = conn.execute("SELECT title, reachability, validated FROM findings").fetchone()
    assert row[0] == "old"
    assert row[1] is None        # legacy default
    assert row[2] == 1           # new column default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kb_schema.py -k "new_columns or migration" -v`
Expected: FAIL — new columns not present (`reproduction` missing)

- [ ] **Step 3: Write minimal implementation**

In `src/reverser/kb/schema.py`, change `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3`.

Add the new columns to the `findings` CREATE TABLE in `_DDL` (so fresh DBs get them directly). Replace the findings block:

```python
    """
    CREATE TABLE IF NOT EXISTS findings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id       TEXT NOT NULL REFERENCES targets(id),
        title           TEXT NOT NULL,
        severity        TEXT NOT NULL,
        cvss            REAL,
        description     TEXT,
        evidence_paths  TEXT,
        reproduction    TEXT,
        reachability    TEXT,
        confidence      INTEGER,
        evidence_blocker TEXT,
        validated       INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL
    )
    """,
```

Add a migration helper and call it from `apply_schema`. Insert before `apply_schema`:

```python
_FINDING_ADDED_COLUMNS = [
    ("reproduction", "TEXT"),
    ("reachability", "TEXT"),
    ("confidence", "INTEGER"),
    ("evidence_blocker", "TEXT"),
    ("validated", "INTEGER NOT NULL DEFAULT 1"),
]


def _migrate_findings_columns(conn: sqlite3.Connection) -> None:
    """Add new finding columns to a pre-v3 table. Idempotent."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(findings)")}
    if not existing:
        return  # table will be created by _DDL
    for name, decl in _FINDING_ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE findings ADD COLUMN {name} {decl}")
```

In `apply_schema`, run the DDL first (creates table if missing) then migrate existing tables. Update the body:

```python
def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if missing, run additive migrations, stamp the version."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    for stmt in _DDL:
        conn.execute(stmt)
    _migrate_findings_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_kb_schema.py -v`
Expected: PASS (existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/schema.py tests/test_kb_schema.py
git commit -m "feat(kb): additive finding columns + schema v3 migration"
```

---

## Task 6: store.py — persist + read new finding fields

**Files:**
- Modify: `src/reverser/kb/store.py`
- Test: `tests/test_kb_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kb_store.py` (use the existing fixture pattern in that file for a temp target; if it uses a `kb` fixture, reuse it — otherwise construct `KB` against a tmp `targets_root`):

```python
from reverser.kb.store import FindingFact


def test_record_and_read_finding_with_new_fields(tmp_kb):
    fid = tmp_kb.record_finding(FindingFact(
        title="rce",
        severity="critical",
        description="d",
        evidence_paths=["findings/x.txt"],
        reproduction="curl ...",
        reachability="demonstrated",
        confidence=90,
        validated=True,
    ))
    assert fid > 0
    out = tmp_kb.get_findings()
    f = next(x for x in out if x.id == fid)
    assert f.reproduction == "curl ..."
    assert f.reachability == "demonstrated"
    assert f.confidence == 90
    assert f.validated is True


def test_get_findings_tolerates_legacy_rows(tmp_kb):
    # write a row directly missing the new fields
    with tmp_kb._connect() as conn:
        conn.execute(
            "INSERT INTO findings (target_id, title, severity, description, evidence_paths, created_at) "
            "VALUES (?, 'legacy', 'low', 'd', '[]', '2026-01-01T00:00:00')",
            (tmp_kb.target_id,),
        )
        conn.commit()
    out = [f for f in tmp_kb.get_findings() if f.title == "legacy"]
    assert len(out) == 1
    assert out[0].reachability is None
    assert out[0].confidence is None
```

If `tests/test_kb_store.py` has no `tmp_kb` fixture, add one to `tests/conftest.py`:

```python
import pytest

@pytest.fixture
def tmp_kb(tmp_path, monkeypatch):
    import reverser.paths as paths
    monkeypatch.setattr(paths, "targets_root", lambda: tmp_path / "targets")
    # KB resolves targets_root via reverser.kb.store import path
    import reverser.kb.store as store
    monkeypatch.setattr(store, "targets_root", lambda: tmp_path / "targets")
    from reverser.kb.store import KB
    return KB("10.0.0.1")
```

(Confirm the actual import name `targets_root` used inside `store.py` before finalizing the monkeypatch target.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kb_store.py -k "new_fields or legacy_rows" -v`
Expected: FAIL — `TypeError: FindingFact.__init__() got an unexpected keyword argument 'reproduction'`

- [ ] **Step 3: Write minimal implementation**

In `src/reverser/kb/store.py`, extend `FindingFact` with optional fields (keep `__post_init__` severity check):

```python
@dataclass
class FindingFact:
    title: str
    severity: str
    description: str
    evidence_paths: list[str] = field(default_factory=list)
    cvss: Optional[float] = None
    reproduction: Optional[str] = None
    reachability: Optional[str] = None
    confidence: Optional[int] = None
    evidence_blocker: Optional[str] = None
    validated: bool = True
    id: Optional[int] = None

    def __post_init__(self):
        if self.severity not in _VALID_SEVERITY:
            raise ValueError(
                f"invalid severity {self.severity!r}; "
                f"must be one of {sorted(_VALID_SEVERITY)}"
            )
```

Update `record_finding` (around line 365) to write the new columns:

```python
    def record_finding(self, finding: FindingFact) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO findings "
                "(target_id, title, severity, cvss, description, evidence_paths, "
                " reproduction, reachability, confidence, evidence_blocker, validated, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.target_id, finding.title, finding.severity, finding.cvss,
                    finding.description, json.dumps(finding.evidence_paths),
                    finding.reproduction, finding.reachability, finding.confidence,
                    finding.evidence_blocker, int(finding.validated), _now_iso(),
                ),
            )
            conn.commit()
            return cur.lastrowid
```

Update `get_findings` (around line 404) to select + populate new columns, tolerating legacy NULLs:

```python
    def get_findings(self, severity: str | None = None) -> list[FindingFact]:
        sql = (
            "SELECT id, title, severity, cvss, description, evidence_paths, "
            "reproduction, reachability, confidence, evidence_blocker, validated "
            "FROM findings WHERE target_id = ?"
        )
        params: list = [self.target_id]
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity)
        out: list[FindingFact] = []
        with self._connect() as conn:
            for r in conn.execute(sql, tuple(params)):
                out.append(FindingFact(
                    id=r[0], title=r[1], severity=r[2], cvss=r[3], description=r[4],
                    evidence_paths=json.loads(r[5]) if r[5] else [],
                    reproduction=r[6], reachability=r[7], confidence=r[8],
                    evidence_blocker=r[9],
                    validated=bool(r[10]) if r[10] is not None else True,
                ))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_kb_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reverser/kb/store.py tests/test_kb_store.py tests/conftest.py
git commit -m "feat(kb): persist and read finding reproduction/reachability/confidence/validated"
```

---

## Task 7: kb_add_finding uses FindingModel

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Test: `tests/test_kb_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kb_tools.py` (async tests; this file already uses `pytest.mark.asyncio` — match its style and any target fixture it defines):

```python
import pytest
from reverser.tools.kb import kb_add_finding


@pytest.mark.asyncio
async def test_kb_add_finding_rejects_missing_reproduction(authorized_target):
    res = await kb_add_finding({
        "target": authorized_target,
        "title": "x", "severity": "high", "description": "d",
        "evidence_paths": ["findings/x.txt"], "confidence": 50,
        "reachability": "likely",
        # reproduction missing
    })
    assert res.get("is_error") is True
    assert "reproduction" in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_kb_add_finding_accepts_valid(authorized_target):
    res = await kb_add_finding({
        "target": authorized_target,
        "title": "x", "severity": "high", "description": "d",
        "evidence_paths": ["findings/x.txt"], "reproduction": "curl",
        "confidence": 50, "reachability": "likely",
    })
    assert res.get("is_error") is not True
    assert "Finding added" in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_kb_add_finding_blocker_path_stores_degraded(authorized_target):
    res = await kb_add_finding({
        "target": authorized_target,
        "title": "x", "severity": "high", "description": "d",
        "evidence_paths": [], "reproduction": "n/a", "confidence": 30,
        "reachability": "demonstrated",
        "evidence_blocker": "target offline",
    })
    assert res.get("is_error") is not True
    assert "unvalidated" in res["content"][0]["text"].lower()
```

(Reuse the file's existing authorization/target fixture; the name `authorized_target` is illustrative — match the file. `_check_auth` must pass, so set `REVERSER_PENTEST_AUTHORIZED=1` or the `.reverser-authorized` marker the other kb-tool tests already rely on.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kb_tools.py -k "add_finding" -v`
Expected: FAIL — current handler accepts missing reproduction (no rejection) / no "unvalidated" text

- [ ] **Step 3: Write minimal implementation**

In `src/reverser/tools/kb.py`, add imports near the top (after existing imports):

```python
from ..schemas.models import FindingModel
from ..schemas.validation import validate_args, tool_input_schema
```

Replace the `@tool("kb_add_finding", ...)` decorator's inline schema dict with the generated one, and rewrite the handler body:

```python
@tool(
    "kb_add_finding",
    "Record a new finding in the KB. Requires evidence_paths (>=1) OR an "
    "evidence_blocker explaining why none exist, plus reproduction, confidence "
    "(0-100), and reachability (demonstrated|likely|theoretical|unknown).",
    tool_input_schema(FindingModel) | {
        "properties": {
            **tool_input_schema(FindingModel)["properties"],
            "target": {"type": "string", "description": "Normalized target identifier."},
        },
        "required": ["target", *tool_input_schema(FindingModel)["required"]],
    },
)
async def kb_add_finding(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args.get("target")
    if not target:
        return format_error("target is required.")
    model_args = {k: v for k, v in args.items() if k != "target"}
    outcome = validate_args(FindingModel, model_args)
    if not outcome.ok:
        return format_error(outcome.error_text)
    m = outcome.value
    finding = FindingFact(
        title=m.title,
        severity=m.severity.value,
        description=m.description,
        evidence_paths=m.evidence_paths,
        cvss=m.cvss,
        reproduction=m.reproduction,
        reachability=m.reachability.value,
        confidence=m.confidence,
        evidence_blocker=m.evidence_blocker,
        validated=m.validated,
    )
    fid = for_target(target).record_finding(finding)
    from ..gui_service.kb_emitter import emit_recorded_finding
    emit_recorded_finding("create", fid, finding)
    suffix = "" if m.validated else " (stored UNVALIDATED — evidence_blocker set)"
    return format_tool_result(f"Finding added: id={fid} title={finding.title!r}{suffix}")
```

(If `emit_recorded_finding` accesses attributes that legacy `FindingFact` lacked, it already tolerates them since they are now real fields. Verify no positional-arg assumptions in `kb_emitter.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_kb_tools.py -k "add_finding" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools): kb_add_finding validates via FindingModel with reject+retry"
```

---

## Task 8: kb_add_hypothesis + kb_update_hypothesis use models

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Test: `tests/test_kb_hypotheses.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kb_hypotheses.py`:

```python
import pytest
from reverser.tools.kb import kb_add_hypothesis, kb_update_hypothesis


@pytest.mark.asyncio
async def test_add_hypothesis_requires_rationale_and_confidence(authorized_target):
    res = await kb_add_hypothesis({
        "target": authorized_target, "statement": "x",
        # rationale + confidence missing
    })
    assert res.get("is_error") is True
    assert "rationale" in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_rejects_illegal_transition(authorized_target):
    add = await kb_add_hypothesis({
        "target": authorized_target, "statement": "y",
        "rationale": "because", "confidence": 40,
    })
    # parse id from "Hypothesis #N added"
    hid = int(add["content"][0]["text"].split("#")[1].split(" ")[0])
    res = await kb_update_hypothesis({
        "target": authorized_target, "id": hid,
        "status": "confirmed",   # proposed -> confirmed is illegal
        "evidence_refs": [{"kind": "finding", "id": 1}],
    })
    assert res.get("is_error") is True
    assert "illegal transition" in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_update_confirmed_requires_evidence(authorized_target):
    add = await kb_add_hypothesis({
        "target": authorized_target, "statement": "z",
        "rationale": "because", "confidence": 40,
    })
    hid = int(add["content"][0]["text"].split("#")[1].split(" ")[0])
    # legal path: proposed -> testing first
    await kb_update_hypothesis({"target": authorized_target, "id": hid, "status": "testing"})
    res = await kb_update_hypothesis({
        "target": authorized_target, "id": hid, "status": "confirmed", "evidence_refs": [],
    })
    assert res.get("is_error") is True
    assert "evidence_refs" in res["content"][0]["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kb_hypotheses.py -k "transition or rationale or requires_evidence" -v`
Expected: FAIL — current handlers accept these without rejection

- [ ] **Step 3: Write minimal implementation**

In `src/reverser/tools/kb.py` add imports:

```python
from ..schemas.models import HypothesisModel, HypothesisUpdateModel
```

Rewrite `kb_add_hypothesis` body (keep its `@tool` schema, but make required match the model):

```python
async def kb_add_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args.get("target")
    if not target:
        return format_error("target is required.")
    outcome = validate_args(HypothesisModel, {k: v for k, v in args.items() if k != "target"})
    if not outcome.ok:
        return format_error(outcome.error_text)
    m = outcome.value
    h = for_target(target).add_hypothesis(
        statement=m.statement,
        parent_id=m.parent_id,
        rationale=m.rationale,
        confidence=m.confidence,
        tags=m.tags,
    )
    from ..gui_service.kb_emitter import emit_hypothesis
    if h is not None:
        emit_hypothesis("create", h)
    return format_tool_result(
        f"Hypothesis #{h.id} added (status={h.status}, confidence={h.confidence}): {h.statement}"
    )
```

Also update the `kb_add_hypothesis` `@tool` schema `required` to `["target", "statement", "rationale", "confidence"]` so the LLM sees them as required up front.

Rewrite `kb_update_hypothesis` body to fetch current status and validate the transition (find the handler after its `@tool` block, lines ~480+):

```python
async def kb_update_hypothesis(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    kb = for_target(args["target"])
    current = kb.get_hypothesis(args["id"])
    if current is None:
        return format_error(f"No hypothesis with id={args['id']}.")
    new_status = args.get("status", current.status)
    outcome = validate_args(HypothesisUpdateModel, {
        "from_status": current.status,
        "to_status": new_status,
        "rationale": args.get("rationale", "") or "",
        "confidence": args.get("confidence"),
        "evidence_refs": args.get("evidence_refs", []) or [],
    })
    if not outcome.ok:
        return format_error(outcome.error_text)
    kb.update_hypothesis(
        args["id"],
        status=args.get("status"),
        rationale=args.get("rationale"),
        confidence=args.get("confidence"),
        dispatched_to=args.get("dispatched_to"),
        evidence_refs=args.get("evidence_refs"),
        tags=args.get("tags"),
    )
    updated = kb.get_hypothesis(args["id"])
    from ..gui_service.kb_emitter import emit_hypothesis
    if updated is not None:
        emit_hypothesis("update", updated)
    return format_tool_result(
        f"Hypothesis #{args['id']} updated (status={updated.status})."
    )
```

(Confirm the current `kb_update_hypothesis` return/emit lines and preserve any existing behavior — e.g. dispatch_count increments are not touched here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_kb_hypotheses.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_hypotheses.py
git commit -m "feat(tools): hypothesis create/update validate via models with transition rules"
```

---

## Task 9: DispatchReportModel + bounded emit+repair

**Files:**
- Modify: `src/reverser/schemas/models.py`, `src/reverser/schemas/__init__.py`
- Modify: `src/reverser/tools/dispatch.py`
- Test: `tests/schemas/test_models.py`, `tests/test_dispatch_helpers.py`

- [ ] **Step 1: Write the failing test (model)**

Append to `tests/schemas/test_models.py`:

```python
from reverser.schemas.models import DispatchReportModel


def test_dispatch_report_valid():
    m = DispatchReportModel(
        tldr="Found weak SMB signing.",
        findings=["SMB signing disabled on DC"],
        hypothesis_outcome="confirmed",
        kb_writes=["finding #1"],
        follow_up=["relay to MSSQL"],
        status="success",
    )
    assert m.hypothesis_outcome == "confirmed"


def test_dispatch_report_bad_outcome_rejected():
    with pytest.raises(ValidationError):
        DispatchReportModel(tldr="x", hypothesis_outcome="maybe", status="success")
```

And a helper test in `tests/test_dispatch_helpers.py`:

```python
from reverser.tools.dispatch import parse_dispatch_report


def test_parse_dispatch_report_from_json_block():
    text = '```json\n{"tldr":"t","hypothesis_outcome":"refuted","status":"success"}\n```'
    outcome, model, errors = parse_dispatch_report(text)
    assert errors is None
    assert outcome == "refuted"
    assert model.tldr == "t"


def test_parse_dispatch_report_invalid_returns_errors():
    outcome, model, errors = parse_dispatch_report("no json here")
    assert model is None
    assert errors is not None
    assert outcome == "inconclusive"   # defensive default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schemas/test_models.py -k dispatch tests/test_dispatch_helpers.py -k parse_dispatch -v`
Expected: FAIL — `DispatchReportModel` / `parse_dispatch_report` undefined

- [ ] **Step 3: Write minimal implementation**

Append to `src/reverser/schemas/models.py`:

```python
class HypothesisOutcome(str, Enum):
    confirmed = "confirmed"
    refuted = "refuted"
    inconclusive = "inconclusive"


class DispatchStatus(str, Enum):
    success = "success"
    partial = "partial"
    error = "error"


class DispatchReportModel(BaseModel):
    """Structured return contract for a dispatched specialist."""

    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    tldr: str = Field(min_length=1)
    findings: list[str] = Field(default_factory=list)
    hypothesis_outcome: HypothesisOutcome = HypothesisOutcome.inconclusive
    kb_writes: list[str] = Field(default_factory=list)
    follow_up: list[str] = Field(default_factory=list)
    status: DispatchStatus = DispatchStatus.success


class ReportModel(BaseModel):
    """Final per-target report assembled from validated KB rows."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    executive_summary: str = Field(min_length=1)
    findings: list[FindingModel] = Field(default_factory=list)
    hosts: int = 0
    services: int = 0
    creds: int = 0
```

Add `DispatchReportModel`, `ReportModel`, `HypothesisOutcome`, `DispatchStatus` to `__init__.py` exports/`__all__`.

In `src/reverser/tools/dispatch.py`, add a JSON-extracting parser near the existing helpers (reuse the same `re`/`json` already imported there):

```python
import json as _json
from ..schemas.models import DispatchReportModel
from ..schemas.validation import validate_args

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    for pat in (_JSON_BLOCK, _BARE_JSON):
        for m in pat.finditer(text or ""):
            try:
                obj = _json.loads(m.group(1))
                if isinstance(obj, dict) and "tldr" in obj:
                    return obj
            except (ValueError, TypeError):
                continue
    return None


def parse_dispatch_report(text: str):
    """Return (outcome, model_or_None, error_text_or_None)."""
    obj = _extract_json(text)
    if obj is None:
        return "inconclusive", None, "No JSON dispatch report block found."
    outcome = validate_args(DispatchReportModel, obj)
    if not outcome.ok:
        return "inconclusive", None, outcome.error_text
    m = outcome.value
    return m.hypothesis_outcome.value, m, None
```

Then, in `dispatch_specialist`, replace the `outcome = parse_hypothesis_outcome(report_text)` + `_has_actionable_findings` block with a bounded emit+repair loop. The existing function already streams a specialist via `query(...)`; wrap the streaming in a local helper `_run_specialist(prompt) -> report_text` (extract the existing streaming body into it), then:

```python
    MAX_DISPATCH_REPAIR = 2
    outcome, model, errors = parse_dispatch_report(report_text)
    attempts = 0
    while errors is not None and attempts < MAX_DISPATCH_REPAIR and status not in ("error",):
        attempts += 1
        repair_prompt = (
            "Your previous report did not include a valid JSON dispatch report. "
            "Re-emit ONLY a ```json block matching this contract: "
            "{tldr, findings[], hypothesis_outcome (confirmed|refuted|inconclusive), "
            "kb_writes[], follow_up[], status (success|partial|error)}.\n"
            f"Errors:\n{errors}"
        )
        report_text = await _run_specialist(repair_prompt)
        outcome, model, errors = parse_dispatch_report(report_text)
    if errors is not None:
        # exhausted: degraded-accept
        status = "partial"
        outcome = outcome or "inconclusive"
```

Keep the existing envelope construction (`summary_lines`), but source `outcome` from the validated model. Remove the `_has_actionable_findings` promotion block (replaced by the explicit degraded-accept above). Leave `parse_hypothesis_outcome`/`_has_actionable_findings` defined for now if other modules import them; otherwise delete and update imports.

(Confirm whether `tests/test_dispatch_helpers.py` references `parse_hypothesis_outcome` — if so, keep that function or update those tests.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schemas/test_models.py tests/test_dispatch_helpers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reverser/schemas/models.py src/reverser/schemas/__init__.py src/reverser/tools/dispatch.py tests/schemas/test_models.py tests/test_dispatch_helpers.py
git commit -m "feat(dispatch): validated DispatchReportModel + bounded emit+repair"
```

---

## Task 10: kb_export_report validated executive_summary + ReportModel render

**Files:**
- Modify: `src/reverser/tools/kb.py`
- Test: `tests/test_kb_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_kb_tools.py`:

```python
from reverser.tools.kb import kb_export_report


@pytest.mark.asyncio
async def test_export_report_requires_executive_summary(authorized_target):
    res = await kb_export_report({"target": authorized_target})
    assert res.get("is_error") is True
    assert "executive_summary" in res["content"][0]["text"]


@pytest.mark.asyncio
async def test_export_report_writes_with_summary(authorized_target, tmp_path):
    out = tmp_path / "report.md"
    res = await kb_export_report({
        "target": authorized_target,
        "executive_summary": "Two findings; DC vulnerable to SMB relay.",
        "output_path": str(out),
    })
    assert res.get("is_error") is not True
    assert out.exists()
    assert "SMB relay" in out.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kb_tools.py -k export_report -v`
Expected: FAIL — current `kb_export_report` does not require/accept `executive_summary`

- [ ] **Step 3: Write minimal implementation**

In `src/reverser/tools/kb.py`, add `executive_summary` to the `kb_export_report` `@tool` schema (required) and validate it. Update the handler:

```python
async def kb_export_report(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    target = args["target"]
    summary = (args.get("executive_summary") or "").strip()
    if not summary:
        return format_error(
            "executive_summary is required (a 1-3 sentence engagement summary). "
            "Resubmit kb_export_report with executive_summary set."
        )
    kb = for_target(target)
    body = _render_report(kb, executive_summary=summary)
    out_path = args.get("output_path") or str(kb.root / "report.md")
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(body)
    return format_tool_result(
        f"Report written to {out_p} ({len(body)} bytes)\n\n--- preview ---\n"
        + body[:2000]
        + ("\n[truncated]" if len(body) > 2000 else "")
    )
```

Update `_render_report` to accept and prepend the summary. Find its `def _render_report(kb` signature and add `executive_summary: str = ""`, then near the top of the rendered body insert:

```python
    if executive_summary:
        lines.append("## Executive summary")
        lines.append("")
        lines.append(executive_summary)
        lines.append("")
```

(Match `_render_report`'s actual local list variable name — it builds the markdown in a list; insert after the title heading.)

Add `"executive_summary": {"type": "string", "description": "1-3 sentence engagement summary."}` to the `kb_export_report` `@tool` properties and add it to `required`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_kb_tools.py -k export_report -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reverser/tools/kb.py tests/test_kb_tools.py
git commit -m "feat(tools): kb_export_report requires validated executive_summary"
```

---

## Task 11: Full regression + cleanup

**Files:** none new — verification only.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green, including pre-existing `tests/tools/test_kb_emit.py` and `tests/test_persona_alignment.py`. Investigate and fix any regressions (most likely: `kb_emitter.py` reading finding fields, or a test asserting old `kb_add_finding` schema text).

- [ ] **Step 2: Verify the tool registry still builds**

Run: `.venv/bin/python -c "from reverser.tools import ALL_TOOLS; print(len(ALL_TOOLS))"`
Expected: prints the tool count with no import error (confirms the new `@tool` schemas via `tool_input_schema` are valid).

- [ ] **Step 3: Confirm no leftover regex parser references**

Run: `grep -rn "parse_hypothesis_outcome\|_has_actionable_findings" src/ tests/`
Expected: either no matches, or only definitions/tests you intentionally kept. Remove dead code and its tests if unused.

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "test: full regression green for schema-validated outputs"
```

---

## Self-Review notes

- **Spec coverage:** Findings (Tasks 1,5,6,7), hypotheses create+update with transitions (Tasks 2,8), dispatch report validate+bounded-repair (Task 9), report with validated executive_summary (Task 10), Pydantic single-source + tool_input_schema (Tasks 1-4), validate-on-write/tolerant-read + migration (Tasks 5,6), backend-parity coercion (Task 3), `$ref` inlining (Task 4). All spec sections map to a task.
- **Blocker escapes:** finding `evidence_blocker` (Task 1/6/7); hypothesis `blocked` status (Task 2/8).
- **Known confirmations the implementer must make before editing** (called out inline): the `targets_root` import name used inside `store.py` for the `tmp_kb` fixture; the existing auth/target fixture name in `tests/test_kb_tools.py` and `tests/test_kb_hypotheses.py`; `_render_report`'s local list variable name; whether `parse_hypothesis_outcome` is referenced elsewhere before deleting it; `kb_emitter.emit_recorded_finding` field access.
