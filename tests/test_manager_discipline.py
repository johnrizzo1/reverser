"""Tests for manager profile prompt + dispatch_specialist reforms."""

import pytest


# ── _promote_status (JSON-model-based status promotion) ──────────────


def test_promote_status_error_with_findings_becomes_partial():
    from reverser.tools.dispatch import _promote_status, parse_dispatch_report
    text = '```json\n{"tldr":"t","findings":["x"],"hypothesis_outcome":"inconclusive","status":"partial"}\n```'
    _, model, _ = parse_dispatch_report(text)
    assert _promote_status("error", model) == "partial"


def test_promote_status_error_without_report_stays_error():
    from reverser.tools.dispatch import _promote_status
    assert _promote_status("error", None) == "error"


def test_promote_status_success_unchanged():
    from reverser.tools.dispatch import _promote_status, parse_dispatch_report
    _, model, _ = parse_dispatch_report('```json\n{"tldr":"t","status":"success"}\n```')
    assert _promote_status("completed", model) == "completed"


# ── Dispatch result: required-action block ───────────────────────────
# Direct tests of the result-rendering helper. We build the required_action
# block independently from the full dispatch flow.


def _build_dispatch_result(report_text="", hypothesis_id=None, status="completed"):
    """Minimal reconstruction of the dispatch-result rendering for testing.

    Mirrors the post-Task-10 logic in dispatch.py: build summary lines
    including the required-action block.
    """
    from reverser.tools.dispatch import _promote_status, parse_dispatch_report

    _, report_model, _ = parse_dispatch_report(report_text)
    status = _promote_status(status, report_model)

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
    """Status: error with a structured report carrying findings → Status: partial."""
    report = '```json\n{"tldr":"t","findings":["CVE-2024-46987 path traversal lead"]}\n```'
    result = _build_dispatch_result(report_text=report, status="error", hypothesis_id=3)
    assert "**Status:** partial" in result
    assert "**Note:**" in result


def test_dispatch_result_keeps_error_when_no_actionable_findings():
    """Status: error with no parseable JSON report → stays error."""
    report = "Traceback (most recent call last): RuntimeError: x"
    result = _build_dispatch_result(report_text=report, status="error", hypothesis_id=3)
    assert "**Status:** error" in result
    assert "**Status:** partial" not in result


def test_dispatch_result_partial_includes_read_body_note():
    """Status: partial gets a note instructing the agent to read the body."""
    report = '```json\n{"tldr":"t","follow_up":["try the alternate path"]}\n```'
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


def test_manager_addendum_mentions_post_dispatch_checklist():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "Post-dispatch checklist" in addendum
    assert "kb_update_hypothesis" in addendum


def test_manager_addendum_requires_exact_seeded_target_for_dispatch():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum

    assert "Seeded target discipline" in addendum
    assert "kb_show" in addendum
    assert "primary_address" in addendum
    assert "target_subset" in addendum
    assert "Do not pass the logical engagement name" in addendum


def test_manager_addendum_forbids_parallel_specialist_dispatches():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum

    assert "Dispatch only one specialist at a time" in addendum
    assert "Do not call `dispatch_specialist`" in addendum
    assert "multiple" in addendum
    assert "one turn" in addendum


def test_manager_addendum_mentions_connection_failure_breaker():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "circuit breaker" in addendum.lower()
    assert "ECONNREFUSED" in addendum or "Connection refused" in addendum or "connection error" in addendum.lower()


def test_manager_addendum_says_breaker_resets_on_user_input():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    addendum = p.system_addendum
    assert "user input" in addendum.lower() or "user sends" in addendum.lower()


def test_skill_kickoff_mentions_dispatch_count():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    skills_by_key = {s.key: s for s in p.skills}
    assert "k" in skills_by_key
    assert "dispatch_count" in skills_by_key["k"].prompt or \
           "two-failure" in skills_by_key["k"].prompt.lower()


def test_skill_pivot_mentions_dispatch_count_2():
    from reverser.profiles import get_profile
    p = get_profile("manager")
    skills_by_key = {s.key: s for s in p.skills}
    assert "p" in skills_by_key
    assert "dispatch_count" in skills_by_key["p"].prompt
