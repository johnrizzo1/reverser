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


# ── bounded emit+repair loop ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_with_repair_succeeds_first_try():
    from reverser.tools.dispatch import _run_with_repair
    good = '```json\n{"tldr":"t","hypothesis_outcome":"confirmed","status":"success"}\n```'
    calls = []

    async def run(p):
        calls.append(p)
        return good

    text, outcome, model, repaired = await _run_with_repair(
        run, "goal", max_repair=2, is_failed_status=lambda: False
    )
    assert repaired == 0 and outcome == "confirmed" and len(calls) == 1


@pytest.mark.asyncio
async def test_run_with_repair_recovers_on_second_attempt():
    from reverser.tools.dispatch import _run_with_repair
    good = '```json\n{"tldr":"t","hypothesis_outcome":"refuted","status":"success"}\n```'
    seq = ["no json here", good]

    async def run(p):
        return seq.pop(0)

    text, outcome, model, repaired = await _run_with_repair(
        run, "goal", max_repair=2, is_failed_status=lambda: False
    )
    assert repaired == 1 and outcome == "refuted" and model is not None


@pytest.mark.asyncio
async def test_run_with_repair_exhausts_and_returns_errors():
    from reverser.tools.dispatch import _run_with_repair

    async def run(p):
        return "never any json"

    text, outcome, model, repaired = await _run_with_repair(
        run, "goal", max_repair=2, is_failed_status=lambda: False
    )
    assert repaired == 2 and model is None  # caller degrades to inconclusive/partial


@pytest.mark.asyncio
async def test_run_with_repair_skips_when_status_failed():
    from reverser.tools.dispatch import _run_with_repair

    async def run(p):
        return "no json"

    text, outcome, model, repaired = await _run_with_repair(
        run, "goal", max_repair=2, is_failed_status=lambda: True
    )
    assert repaired == 0  # don't waste budget repairing a failed run


@pytest.mark.asyncio
async def test_run_with_repair_invokes_on_repair_callback():
    from reverser.tools.dispatch import _run_with_repair
    good = '```json\n{"tldr":"t","hypothesis_outcome":"confirmed","status":"success"}\n```'
    seq = ["no json", "still no json", good]
    seen = []

    async def run(p):
        return seq.pop(0)

    text, outcome, model, repaired = await _run_with_repair(
        run, "goal", max_repair=2, is_failed_status=lambda: False,
        on_repair=lambda a: seen.append(a),
    )
    assert repaired == 2 and seen == [1, 2] and outcome == "confirmed"


def test_repair_prompt_embeds_report_and_errors():
    from reverser.tools.dispatch import _repair_prompt
    p = _repair_prompt("PRIOR REPORT BODY", "missing tldr")
    assert "PRIOR REPORT BODY" in p
    assert "missing tldr" in p
    assert "Do NOT investigate further" in p
    assert "```json" in p


def test_promote_status_error_with_empty_model_stays_error():
    from reverser.tools.dispatch import _promote_status, parse_dispatch_report
    _, model, _ = parse_dispatch_report('```json\n{"tldr":"t","status":"error"}\n```')
    assert _promote_status("error", model) == "error"
