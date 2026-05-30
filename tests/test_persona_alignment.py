"""Regression tests for cross-persona objective and manager/specialist alignment."""

from reverser.profiles import get_profile
from reverser.prompts import OBJECTIVE_ALIGNMENT_PROMPT, PROFILE_OPERATING_CONTRACT


def test_shared_contract_requires_domain_classification_before_tools():
    text = PROFILE_OPERATING_CONTRACT.lower()
    assert "classify the task domain" in text
    assert "binary" in text
    assert "network" in text
    assert "web" in text
    assert "do not run binary-analysis tools" in text


def test_shared_contract_requires_persisting_summarized_findings():
    text = PROFILE_OPERATING_CONTRACT.lower()
    assert "kb_add_finding" in text
    assert "findings tab" in text
    assert "prose" in text


def test_objective_alignment_prioritizes_current_user_task_over_profile():
    text = OBJECTIVE_ALIGNMENT_PROMPT.lower()
    assert "current objective is authoritative" in text
    assert "selected profile is a lens" in text
    assert "not a mandate" in text
    assert "do not force" in text


def test_manager_addendum_defines_control_loop_before_delegation():
    addendum = get_profile("manager").system_addendum
    assert "Manager control loop" in addendum
    assert "restating the user's objective" in addendum
    assert "Do not dispatch until" in addendum
    assert "bounded target_subset" in addendum


def test_manager_addendum_requires_specialist_selection_rationale():
    addendum = get_profile("manager").system_addendum
    assert "specialist selection rationale" in addendum.lower()
    assert "why this specialist" in addendum.lower()


def test_specialist_profiles_have_standard_report_contract():
    for key in ("pentest", "webpentest", "ad", "exploit"):
        addendum = get_profile(key).system_addendum
        assert "Specialist reporting contract" in addendum, key
        assert "Hypothesis outcome" in addendum, key
        assert "Evidence" in addendum, key
        assert "Suggested follow-up" in addendum, key
