"""Tests for dispatch -> KB reconciliation (backstop persistence of specialist
report findings/hypotheses)."""

from reverser.tools.dispatch import parse_report_kb_writes, reconcile_report_to_kb


def _fresh_kb(tmp_path, monkeypatch, target="recon-target"):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb.store import KB
    return KB(target)


# ── parse_report_kb_writes ───────────────────────────────────────────


def test_parse_kb_writes_extracts_findings_and_hypotheses():
    report = (
        "## TL;DR\nok\n\n"
        "### KB writes\n"
        "- Finding: Nginx UI 2.3.2 on admin.snapped.htb — admin panel exposed\n"
        "- **Finding:** Web server nginx/1.24.0 on Ubuntu\n"
        "- Hypothesis: Nginx UI is exploitable via a known CVE\n"
        "- Hypothesis spawned: backup endpoint leaks AES key\n\n"
        "### Suggested follow-up\n- try CVE\n"
    )
    findings, hyps = parse_report_kb_writes(report)
    titles = [f["title"] for f in findings]
    # title is the text before the ' — ' separator, capped at 120 chars
    assert "Nginx UI 2.3.2 on admin.snapped.htb" in titles
    assert any("Web server nginx/1.24.0" in t for t in titles)
    assert "Nginx UI is exploitable via a known CVE" in hyps
    assert "backup endpoint leaks AES key" in hyps
    # the follow-up bullet is not a labeled finding/hypothesis
    assert all("try CVE" not in t for t in titles)


def test_parse_kb_writes_empty_when_no_section():
    findings, hyps = parse_report_kb_writes("## TL;DR\nnothing structured here")
    assert findings == [] and hyps == []


def test_parse_kb_writes_ignores_non_labeled_bullets():
    report = "### KB writes\n- some random note\n- Finding: real one\n"
    findings, hyps = parse_report_kb_writes(report)
    assert [f["title"] for f in findings] == ["real one"]
    assert hyps == []


# ── reconcile_report_to_kb ───────────────────────────────────────────


def test_reconcile_persists_new_finding_unvalidated(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    actions = reconcile_report_to_kb(
        kb,
        [{"title": "SQLi in login", "description": "id param is concatenated"}],
        [],
        specialty="webpentest",
    )
    fs = kb.get_findings()
    assert len(fs) == 1
    assert fs[0].title == "SQLi in login"
    assert fs[0].validated is False  # stored unvalidated (no evidence attached)
    assert "dispatch" in (fs[0].evidence_blocker or "").lower()
    assert any("finding #" in a.lower() for a in actions)


def test_reconcile_dedups_existing_finding(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    reconcile_report_to_kb(kb, [{"title": "Dup", "description": "x"}], [], specialty="pentest")
    # same title (normalized, extra whitespace) must not create a second row
    reconcile_report_to_kb(kb, [{"title": "  dup  ", "description": "y"}], [], specialty="pentest")
    assert len(kb.get_findings()) == 1


def test_reconcile_dedups_within_batch(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    reconcile_report_to_kb(
        kb,
        [{"title": "Same", "description": "a"}, {"title": "same", "description": "b"}],
        [],
        specialty="pentest",
    )
    assert len(kb.get_findings()) == 1


def test_reconcile_persists_new_hypothesis_proposed(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    reconcile_report_to_kb(kb, [], ["DC allows unsigned SMB"], specialty="ad")
    hyps = kb.list_hypotheses()
    assert len(hyps) == 1 and hyps[0].status == "proposed"


def test_reconcile_dedups_existing_hypothesis(tmp_path, monkeypatch):
    kb = _fresh_kb(tmp_path, monkeypatch)
    kb.add_hypothesis(statement="Existing hyp", rationale="r", confidence=50)
    reconcile_report_to_kb(kb, [], ["existing hyp"], specialty="ad")  # normalized dup
    assert len(kb.list_hypotheses()) == 1
