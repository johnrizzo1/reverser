"""Manager-profile dispatch tool: spawn specialist sub-agents via the SDK.

Pure helpers (compose_dispatch_context, parse_hypothesis_outcome) are
unit-tested in isolation. The dispatch_specialist tool itself wraps these
helpers around an SDK Task call (see Task 13).
"""

from __future__ import annotations

import re


# ── Pure helpers ────────────────────────────────────────────────────


_RETURN_CONTRACT = """## Return contract
When you finish, your final assistant message MUST be a markdown report
with these sections:

### TL;DR
One sentence.

### Findings
What you discovered. Bullet list.

### Hypothesis outcome
One of: CONFIRMED, REFUTED, INCONCLUSIVE — followed by one-sentence justification.

### KB writes
Short list of what you persisted (creds added, findings added, hypotheses
spawned). The lead reads this to know what changed.

### Suggested follow-up
What you would test next if you had more budget. The lead decides whether
to act on it.
"""


def compose_dispatch_context(
    *,
    target: str,
    sub_goal: str,
    target_subset: list[str] | None,
    hypothesis_id: int | None,
    hypothesis_statement: str | None,
    rationale: str | None,
    scope_summary: str | None,
    max_turns: int,
    budget_usd: float,
    extra_context: str | None,
) -> str:
    """Compose the dispatch-context block prepended to a specialist's system prompt."""
    subset_line = (
        ", ".join(target_subset)
        if target_subset
        else "entire target scope"
    )
    hyp_line = (
        f"id={hypothesis_id}: {hypothesis_statement or '(statement not provided)'}"
        if hypothesis_id is not None
        else "(no hypothesis attached — lead did not link this dispatch)"
    )
    scope_line = scope_summary if scope_summary else "(no scope.toml present — default conservative behavior)"
    rationale_line = rationale if rationale else "(none provided)"
    extra_line = extra_context if extra_context else "(none)"

    return f"""# Dispatch context (read first)

You are operating as a sub-agent of the engagement lead.

- Engagement target: {target}
- Sub-goal: {sub_goal}
- Target subset: {subset_line}
- Hypothesis under test ({hyp_line})
- Rationale from lead: {rationale_line}
- Extra context: {extra_line}

## Scope envelope (do not exceed)
{scope_line}

## Per-dispatch budget
- Max turns: {max_turns}
- Cost cap: ${budget_usd:.2f}

{_RETURN_CONTRACT}
"""


_OUTCOME_KEYWORDS = {
    "confirmed": "confirmed",
    "refuted": "refuted",
    "inconclusive": "inconclusive",
}


def parse_hypothesis_outcome(report: str) -> str | None:
    """Extract the outcome word from the '### Hypothesis outcome' section.

    Returns one of {'confirmed', 'refuted', 'inconclusive'}, or None if the
    section is missing entirely. If the section exists but the value is
    unparseable, returns 'inconclusive' (defensive — the lead can re-dispatch).
    """
    # Find the section header (case-insensitive, allow extra whitespace)
    pattern = re.compile(
        r"###\s+Hypothesis\s+outcome\s*\n(.+?)(?=\n###|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(report)
    if not match:
        return None

    body = match.group(1).strip()
    if not body:
        return "inconclusive"

    # Look for the first matching outcome keyword in the body
    body_lower = body.lower()
    for keyword in _OUTCOME_KEYWORDS:
        if keyword in body_lower:
            return _OUTCOME_KEYWORDS[keyword]

    # Section present but no keyword recognized
    return "inconclusive"
