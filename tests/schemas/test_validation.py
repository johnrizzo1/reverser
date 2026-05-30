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
