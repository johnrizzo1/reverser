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


def test_demonstrated_without_evidence_is_rejected():
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    kw["reachability"] = "demonstrated"
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
    assert m.validated is False and m.reachability == Reachability.theoretical


def test_likely_without_evidence_still_rejected_generically():
    kw = _valid_finding_kwargs()
    kw["evidence_paths"] = []
    kw["reachability"] = "likely"
    with pytest.raises(ValidationError):
        FindingModel(**kw)
