"""Pure-function tests for the dispatch prompt composer + outcome parser."""

import pytest

from reverser.tools.dispatch import (
    compose_dispatch_context,
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


# ── markdown fallback for outcome parsing ────────────────────────────
# Specialists reliably emit markdown but often omit the JSON block, so
# parse_dispatch_report must recover the outcome from the markdown
# "### Hypothesis outcome" section WITHOUT treating its absence as a failure
# (a missing JSON block must NOT trigger re-running the specialist).


def test_parse_dispatch_report_prefers_json_block():
    from reverser.tools.dispatch import parse_dispatch_report
    text = (
        "### Hypothesis outcome\nINCONCLUSIVE\n\n"
        '```json\n{"tldr":"t","hypothesis_outcome":"confirmed","status":"success"}\n```'
    )
    outcome, model, errors = parse_dispatch_report(text)
    assert outcome == "confirmed" and model is not None and errors is None


def test_parse_dispatch_report_falls_back_to_markdown_outcome():
    from reverser.tools.dispatch import parse_dispatch_report
    text = (
        "## TL;DR\nFound it.\n\n"
        "### Hypothesis outcome\nCONFIRMED — creds work over SMB.\n\n"
        "### KB writes\n- Finding: weak SMB signing\n"
    )
    outcome, model, errors = parse_dispatch_report(text)
    # markdown outcome recovered, no JSON block needed, NOT treated as a failure
    assert outcome == "confirmed"
    assert model is None
    assert errors is None


def test_parse_dispatch_report_markdown_refuted():
    from reverser.tools.dispatch import parse_dispatch_report
    outcome, model, errors = parse_dispatch_report("### Hypothesis outcome\nREFUTED\n")
    assert outcome == "refuted" and errors is None


def test_parse_dispatch_report_no_outcome_anywhere():
    from reverser.tools.dispatch import parse_dispatch_report
    outcome, model, errors = parse_dispatch_report("just some prose, no sections")
    assert outcome == "inconclusive" and model is None and errors is not None


def test_promote_status_error_with_empty_model_stays_error():
    from reverser.tools.dispatch import _promote_status, parse_dispatch_report
    _, model, _ = parse_dispatch_report('```json\n{"tldr":"t","status":"error"}\n```')
    assert _promote_status("error", model) == "error"
