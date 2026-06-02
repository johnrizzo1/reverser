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
    kb.record_finding(FindingFact(title="Legacy item", severity="low",
        description="d", evidence_paths=["x"], reproduction="r"))
    return kb


def test_render_report_tiers_findings(tmp_path, monkeypatch):
    from reverser.tools.kb import _render_report
    kb = _seed_kb(tmp_path, monkeypatch)
    out = _render_report(kb)
    assert "## Confirmed Findings" in out
    assert "## Unproven / Needs Verification" in out
    conf = out.split("## Confirmed Findings", 1)[1].split("## Unproven", 1)[0]
    assert "Proven RCE" in conf and "demonstrated" in conf
    assert "Maybe SQLi" not in conf and "Legacy item" not in conf
    unp = out.split("## Unproven / Needs Verification", 1)[1]
    assert "Maybe SQLi" in unp and "Legacy item" in unp
    assert "1 confirmed" in out and "2 unproven" in out


def test_render_report_empty_tiers(tmp_path, monkeypatch):
    from reverser.tools.kb import _render_report
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb; reverser.kb._kb_cache.clear()
    from reverser.kb.store import KB
    out = _render_report(KB("empty"))
    assert "## Confirmed Findings" in out and "## Unproven / Needs Verification" in out
    assert out.count("_None._") >= 2
