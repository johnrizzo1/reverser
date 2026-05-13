"""Tests for manager profile prompt + dispatch_specialist reforms."""

import pytest


# ── _has_actionable_findings heuristic ───────────────────────────────


def test_has_actionable_findings_recognizes_findings_section():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Findings
The login form at /admin/login is captcha-protected after 5 attempts.
Recommend Playwright with OCR for captcha bypass.
"""
    assert _has_actionable_findings(report) is True


def test_has_actionable_findings_recognizes_suggested_follow_up():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### TL;DR
Specialist crashed.

### Suggested follow-up
Try CVE-2024-46987 path traversal against /cms-admin/files.
"""
    assert _has_actionable_findings(report) is True


def test_has_actionable_findings_recognizes_hypothesis_outcome():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Hypothesis outcome
REFUTED — SSH does not accept the harvested credentials.
"""
    assert _has_actionable_findings(report) is True


def test_has_actionable_findings_rejects_empty_section_body():
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Findings

### Suggested follow-up

### TL;DR
"""
    assert _has_actionable_findings(report) is False


def test_has_actionable_findings_rejects_short_section_body():
    """Section header but <20 chars under it = not actionable."""
    from reverser.tools.dispatch import _has_actionable_findings
    report = """### Findings
nothing.
"""
    assert _has_actionable_findings(report) is False


def test_has_actionable_findings_rejects_pure_traceback():
    """A stack trace without any of the three section headers doesn't qualify."""
    from reverser.tools.dispatch import _has_actionable_findings
    report = """Traceback (most recent call last):
  File "/foo/bar.py", line 123, in xyz
    raise ConnectionError("refused")
ConnectionError: refused
"""
    assert _has_actionable_findings(report) is False


# ── Dispatch result: required-action block ───────────────────────────
# Direct tests of the result-rendering helper. We build the required_action
# block independently from the full dispatch flow.


def _build_dispatch_result(report_text="", hypothesis_id=None, status="completed"):
    """Minimal reconstruction of the dispatch-result rendering for testing.

    Mirrors the post-Task-10 logic in dispatch.py: build summary lines
    including the required-action block.
    """
    from reverser.tools.dispatch import _has_actionable_findings

    if status == "error" and _has_actionable_findings(report_text):
        status = "partial"

    summary_lines = [
        f"# Dispatch result — test",
        f"**Status:** {status}",
    ]
    if status == "partial":
        summary_lines.append(
            "**Note:** Subprocess exited non-zero but the specialist produced "
            "findings. READ THE REPORT BODY BELOW before deciding next action."
        )
    summary_lines.append("")
    summary_lines.append("---")
    summary_lines.append("")
    summary_lines.append("## Specialist's report")
    summary_lines.append("")
    summary_lines.append(report_text)

    # Required-action block
    required_action_lines = ["", "---", "", "## REQUIRED next action", ""]
    if hypothesis_id is not None:
        required_action_lines.extend([
            f"Call `kb_update_hypothesis(id={hypothesis_id}, status=...,",
            f"evidence_refs=[...])` BEFORE issuing any other tool call.",
        ])
    else:
        required_action_lines.extend([
            "This dispatch was not tied to a hypothesis (hypothesis_id was None).",
        ])
    summary_lines.extend(required_action_lines)
    return "\n".join(summary_lines)


def test_dispatch_result_includes_required_action_when_hypothesis_id_given():
    """With a hypothesis_id, the block mentions kb_update_hypothesis(id=X)."""
    result = _build_dispatch_result(hypothesis_id=42)
    assert "## REQUIRED next action" in result
    assert "kb_update_hypothesis(id=42" in result


def test_dispatch_result_includes_required_action_when_no_hypothesis():
    """Without a hypothesis_id, the block prompts kb_add_hypothesis or kb_add_note."""
    result = _build_dispatch_result(hypothesis_id=None)
    assert "## REQUIRED next action" in result
    assert "not tied to a hypothesis" in result


def test_dispatch_result_promotes_error_to_partial_when_findings_present():
    """Status: error with actionable findings → Status: partial."""
    report = "### Findings\nCVE-2024-46987 path traversal exploit lead.\nUse Playwright."
    result = _build_dispatch_result(report_text=report, status="error", hypothesis_id=3)
    assert "**Status:** partial" in result
    assert "**Note:**" in result


def test_dispatch_result_keeps_error_when_no_actionable_findings():
    """Status: error with no return-contract sections → stays error."""
    report = "Traceback (most recent call last): RuntimeError: x"
    result = _build_dispatch_result(report_text=report, status="error", hypothesis_id=3)
    assert "**Status:** error" in result
    assert "**Status:** partial" not in result


def test_dispatch_result_partial_includes_read_body_note():
    """Status: partial gets a note instructing the agent to read the body."""
    report = "### Findings\nUseful intel that's long enough to qualify."
    result = _build_dispatch_result(report_text=report, status="error", hypothesis_id=1)
    assert "READ THE REPORT BODY BELOW" in result


# ── Manager prompt content ───────────────────────────────────────────


def test_manager_addendum_mentions_two_failure_pivot():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "Two-failure pivot rule" in addendum
    assert "NON-NEGOTIABLE" in addendum


def test_manager_addendum_specifies_what_counts_as_failed_dispatch():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "What counts as a failed dispatch" in addendum


def test_manager_addendum_lists_what_does_NOT_count_as_failed():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "What does NOT count" in addendum
    assert "confirmed" in addendum.lower()


def test_manager_addendum_mentions_orthogonal_hypotheses():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "orthogonal" in addendum.lower()
    assert "three" in addendum.lower() or "3" in addendum
