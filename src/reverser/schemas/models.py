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
