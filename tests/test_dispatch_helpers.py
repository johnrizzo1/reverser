"""Pure-function tests for the dispatch prompt composer + outcome parser."""

import pytest

from reverser.tools.dispatch import (
    compose_dispatch_context,
    parse_hypothesis_outcome,
)


def test_compose_dispatch_context_includes_all_fields():
    block = compose_dispatch_context(
        target="10.10.10.5",
        sub_goal="Enumerate SMB shares",
        target_subset=["10.10.10.5"],
        hypothesis_id=42,
        hypothesis_statement="DC has SMB signing disabled",
        rationale="From nmap output",
        scope_summary="In-scope: 10.10.10.0/24",
        max_turns=15,
        budget_usd=0.50,
        extra_context=None,
    )
    assert "10.10.10.5" in block
    assert "Enumerate SMB shares" in block
    assert "id=42" in block
    assert "DC has SMB signing disabled" in block
    assert "From nmap output" in block
    assert "In-scope: 10.10.10.0/24" in block
    assert "Max turns: 15" in block
    assert "$0.50" in block
    # Return contract sections must be specified
    assert "### TL;DR" in block
    assert "### Findings" in block
    assert "### Hypothesis outcome" in block
    assert "### KB writes" in block
    assert "### Suggested follow-up" in block


def test_compose_dispatch_context_requires_exact_target_for_tool_calls():
    block = compose_dispatch_context(
        target="10.10.10.5",
        sub_goal="Enumerate SMB shares",
        target_subset=["10.10.10.5"],
        hypothesis_id=42,
        hypothesis_statement="SMB exposure exists",
        rationale="Initial KB seed identified this host",
        scope_summary=None,
        max_turns=15,
        budget_usd=0.50,
        extra_context=None,
    )

    assert "Use the engagement target exactly as provided" in block
    assert "10.10.10.5" in block
    assert "Target subset is the active scope for this dispatch" in block
    assert "Do not substitute the logical engagement name" in block


def test_compose_dispatch_context_handles_missing_optional_fields():
    block = compose_dispatch_context(
        target="x",
        sub_goal="y",
        target_subset=None,
        hypothesis_id=None,
        hypothesis_statement=None,
        rationale=None,
        scope_summary=None,
        max_turns=15,
        budget_usd=0.50,
        extra_context=None,
    )
    # Should not crash; placeholders for missing fields
    assert "y" in block
    assert "entire target scope" in block.lower() or "no subset" in block.lower()


def test_parse_hypothesis_outcome_confirmed():
    report = """### TL;DR
Found it.

### Hypothesis outcome
CONFIRMED — credentials work via SMB to the DC.

### KB writes
- Added cred #5 (status=valid)
"""
    outcome = parse_hypothesis_outcome(report)
    assert outcome == "confirmed"


def test_parse_hypothesis_outcome_refuted():
    report = "### Hypothesis outcome\nREFUTED — anonymous LDAP rejected with 0x80070005.\n"
    assert parse_hypothesis_outcome(report) == "refuted"


def test_parse_hypothesis_outcome_inconclusive():
    report = "### Hypothesis outcome\nINCONCLUSIVE — service was unreachable.\n"
    assert parse_hypothesis_outcome(report) == "inconclusive"


def test_parse_hypothesis_outcome_missing_section_returns_none():
    report = "### TL;DR\nDid stuff.\n"
    assert parse_hypothesis_outcome(report) is None


def test_parse_hypothesis_outcome_unparseable_value_returns_inconclusive():
    """When the section exists but value is gibberish, default to inconclusive."""
    report = "### Hypothesis outcome\n¯\\_(ツ)_/¯ no clear answer\n"
    assert parse_hypothesis_outcome(report) == "inconclusive"


def test_parse_hypothesis_outcome_case_insensitive():
    report = "### Hypothesis outcome\nconfirmed — works.\n"
    assert parse_hypothesis_outcome(report) == "confirmed"


def test_parse_dispatch_report_from_json_block():
    from reverser.tools.dispatch import parse_dispatch_report
    text = '```json\n{"tldr":"t","hypothesis_outcome":"refuted","status":"success"}\n```'
    outcome, model, errors = parse_dispatch_report(text)
    assert errors is None
    assert outcome == "refuted"
    assert model.tldr == "t"


def test_parse_dispatch_report_invalid_returns_errors():
    from reverser.tools.dispatch import parse_dispatch_report
    outcome, model, errors = parse_dispatch_report("no json here")
    assert model is None
    assert errors is not None
    assert outcome == "inconclusive"   # defensive default
