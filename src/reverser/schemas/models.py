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
