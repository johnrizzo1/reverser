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

## Target discipline
Use the engagement target exactly as provided for tool calls unless the
target subset narrows it. Target subset is the active scope for this dispatch:
{subset_line}. Do not substitute the logical engagement name, a remembered
nickname, or a different host from prior context.

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


# ── Status: partial heuristic (per spec D4) ──────────────────────────


_PARTIAL_HEURISTIC_PATTERN = re.compile(
    r"###\s+(Findings|Suggested follow-up|Hypothesis outcome)\s*\n((?:(?!###).)*)",
    re.IGNORECASE | re.DOTALL,
)


def _has_actionable_findings(report: str) -> bool:
    """Return True if the report body contains at least one return-contract
    section with non-trivial content (>=20 chars).

    Used by dispatch_specialist to promote Status: error → Status: partial
    when a subprocess errored but the specialist still produced useful intel.
    Heuristic matches against the section headers from `_RETURN_CONTRACT`:
    Findings, Suggested follow-up, Hypothesis outcome.

    See docs/superpowers/specs/2026-05-12-manager-reliability-design.md §7.
    """
    if not report:
        return False
    for match in _PARTIAL_HEURISTIC_PATTERN.finditer(report):
        body = match.group(2).strip()
        if len(body) >= 20:
            return True
    return False


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


# ── dispatch_specialist tool ────────────────────────────────────────

from claude_agent_sdk import (  # noqa: E402  # imports here to keep helpers import-light
    tool,
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    UserMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)

from .kb import _check_auth, format_tool_result  # reuse existing helpers
from ..profiles import get_profile  # noqa: E402
from ..kb.store import KB  # noqa: E402
from ..kb.scope import load_scope  # noqa: E402


_DISPATCHABLE_SPECIALTIES = (
    "pentest", "ad", "webpentest", "webapi", "webrecon",
    "exploit",
)

TOOLS: list = []  # exported for tools/__init__.py registration


@tool(
    "dispatch_specialist",
    "Dispatch a specialist sub-agent to test a hypothesis or perform a sub-goal. "
    "Use this when the manager profile needs offensive work done — the sub-agent "
    "runs with its own context, budget cap, and full tool surface (minus this "
    "tool to prevent recursive dispatch). Specialty must be one of: "
    "pentest, ad, webpentest, webapi, webrecon. Returns a structured envelope "
    "containing the specialist's markdown report, hypothesis_outcome (parsed "
    "from the report), cost, and turns consumed.",
    {
        "type": "object",
        "properties": {
            "specialty": {
                "type": "string",
                "enum": list(_DISPATCHABLE_SPECIALTIES),
            },
            "sub_goal": {"type": "string", "description": "One-sentence falsifiable objective."},
            "target": {"type": "string", "description": "Target identifier."},
            "target_subset": {
                "type": "array", "items": {"type": "string"},
                "description": "Specific hosts/URLs (default: entire target scope).",
            },
            "hypothesis_id": {"type": "integer", "description": "Hypothesis being tested (strongly recommended)."},
            "rationale": {"type": "string", "description": "Why dispatching now (audit-log)."},
            "budget_usd": {"type": "number", "default": 0.50},
            "max_turns": {"type": "integer", "default": 15},
            "extra_context": {"type": "string", "description": "Additional briefing for the specialist."},
        },
        "required": ["specialty", "sub_goal", "target"],
    },
)
async def dispatch_specialist(args: dict) -> dict:
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    specialty = args["specialty"]
    if specialty not in _DISPATCHABLE_SPECIALTIES:
        return format_tool_result(
            f"Unknown or invalid (non-dispatchable) specialty: {specialty!r}. "
            f"Valid: {list(_DISPATCHABLE_SPECIALTIES)}"
        )

    target = args["target"]
    sub_goal = args["sub_goal"]
    hypothesis_id = args.get("hypothesis_id")
    rationale = args.get("rationale")
    target_subset = args.get("target_subset")
    extra_context = args.get("extra_context")
    budget_usd = float(args.get("budget_usd", 0.50))
    max_turns = int(args.get("max_turns", 15))

    # Look up hypothesis (if any) for the dispatch context, and mark it as testing
    kb = KB(target)
    hypothesis_statement = None
    if hypothesis_id is not None:
        h = kb.get_hypothesis(hypothesis_id)
        if h is not None:
            hypothesis_statement = h.statement
            kb.update_hypothesis(
                hypothesis_id,
                status="testing",
                dispatched_to=specialty,
                increment_dispatch_count=True,
            )

    # Build the scope summary (if a scope.toml exists)
    try:
        scope = load_scope(target)
        scope_summary = (
            f"in_scope_cidrs={scope.in_scope_cidrs}; "
            f"no_dos={scope.no_dos}; no_account_lockout={scope.no_account_lockout}; "
            f"allowed_hours={scope.allowed_hours}"
        )
    except Exception:
        scope_summary = None

    # Compose system prompt: dispatch context + specialty addendum
    profile = get_profile(specialty)
    dispatch_block = compose_dispatch_context(
        target=target,
        sub_goal=sub_goal,
        target_subset=target_subset,
        hypothesis_id=hypothesis_id,
        hypothesis_statement=hypothesis_statement,
        rationale=rationale,
        scope_summary=scope_summary,
        max_turns=max_turns,
        budget_usd=budget_usd,
        extra_context=extra_context,
    )
    full_system_prompt = dispatch_block + "\n\n" + profile.system_addendum

    # Compute the sub-agent's allowed_tools — all MCP tools EXCEPT
    # dispatch_specialist (no recursive dispatch). Late import to avoid cycle.
    from . import ALL_TOOLS  # noqa: E402  # local to break cycle
    sub_allowed_tools = [
        f"mcp__re__{t.name}" for t in ALL_TOOLS if t.name != "dispatch_specialist"
    ]

    options = ClaudeAgentOptions(
        system_prompt=full_system_prompt,
        allowed_tools=sub_allowed_tools,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        max_budget_usd=budget_usd,
    )

    # Mark in_flight on the running session's snapshot so resume tooling
    # can surface "stopped mid-dispatch" if the user stops here.
    from datetime import datetime, timezone
    from ..sessions import current_session, InFlightDispatch, save as save_snapshot
    sess = current_session.get()
    if sess is not None:
        sess._snapshot.in_flight = InFlightDispatch(
            kind="dispatch",
            specialty=specialty,
            hypothesis_id=hypothesis_id,
            sub_goal=sub_goal,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        try:
            save_snapshot(sess._snapshot)
        except Exception:
            pass

    report_text = ""
    cost_usd = 0.0
    turns_consumed = 0
    status = "completed"
    error_msg = None

    # Helper to push a sub-agent event up to the TUI (no-op when no session
    # or no callback is registered). Truncate tool_result bodies to keep the
    # chat log readable; the full body is still in the session log.
    import uuid as _uuid
    import json as _json
    dispatch_id = _uuid.uuid4().hex
    _sub_turn = [0]

    def _emit(kind: str, content: str) -> None:
        if sess is None:
            return
        try:
            sess._slog.log_dispatch_event(
                specialty, kind, content,
                dispatch_id=dispatch_id, sub_turn=_sub_turn[0],
            )
        except TypeError:
            try:
                sess._slog.log_dispatch_event(specialty, kind, content)
            except Exception:
                pass
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, _sub_turn[0], kind, content,
            )
        except Exception:
            pass

    def _emit_start() -> None:
        if sess is None:
            return
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, 0, "start",
                _json.dumps({
                    "hypothesis_id": hypothesis_id,
                    "sub_goal": sub_goal,
                }),
            )
        except Exception:
            pass

    def _emit_end(status: str, cost: float, turns_consumed: int) -> None:
        if sess is None:
            return
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, 0, "end",
                _json.dumps({
                    "status": status,
                    "cost": cost,
                    "turns": turns_consumed,
                }),
            )
        except Exception:
            pass

    def _summarize_tool_input(tool_input: object) -> str:
        try:
            import json as _json
            s = _json.dumps(tool_input, default=str, ensure_ascii=False)
        except Exception:
            s = str(tool_input)
        return s if len(s) <= 200 else s[:200] + "…"

    def _summarize_tool_result(content: object) -> str:
        # content may be str, list of dicts, or a list of content blocks
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or item))
                else:
                    parts.append(str(item))
            text = "\n".join(parts)
        else:
            text = str(content)
        text = text.strip()
        return text if len(text) <= 400 else text[:400] + "…"

    _emit_start()
    try:
        async for message in query(prompt=sub_goal, options=options):
            if isinstance(message, AssistantMessage):
                _sub_turn[0] += 1
                for block in message.content:
                    if isinstance(block, TextBlock):
                        report_text = block.text
                        if block.text.strip():
                            _emit("text", block.text)
                    elif isinstance(block, ThinkingBlock):
                        thinking_text = getattr(block, "thinking", "") or ""
                        if thinking_text.strip():
                            _emit("thinking", thinking_text)
                    elif isinstance(block, ToolUseBlock):
                        tool_name = getattr(block, "name", "?")
                        tool_input = getattr(block, "input", {})
                        _emit(
                            "tool_call",
                            f"{tool_name} {_summarize_tool_input(tool_input)}",
                        )
            elif isinstance(message, UserMessage):
                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            kind = "tool_error" if getattr(block, "is_error", False) else "tool_result"
                            _emit(kind, _summarize_tool_result(getattr(block, "content", "")))
            elif isinstance(message, ResultMessage):
                cost_usd = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
                turns_consumed = int(getattr(message, "num_turns", 0) or 0)
                if message.subtype != "success":
                    subtype_str = str(message.subtype).lower()
                    if "budget" in subtype_str:
                        status = "budget_exhausted"
                    elif "turn" in subtype_str:
                        status = "turn_limit"
                    else:
                        status = "error"
                    if not report_text:
                        report_text = (
                            f"(specialist did not produce a report; "
                            f"subtype={message.subtype})"
                        )
    except Exception as e:
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"
        if not report_text:
            report_text = f"(dispatch failed: {error_msg})"
    finally:
        _emit_end(status, cost_usd, turns_consumed)
        if sess is not None:
            sess._snapshot.in_flight = None
            try:
                save_snapshot(sess._snapshot)
            except Exception:
                pass

    outcome = parse_hypothesis_outcome(report_text)

    # ── Status: partial promotion (per spec D4) ──────────────────────
    # If subprocess errored but the report body has return-contract sections
    # with actionable content, promote status so the manager doesn't dismiss
    # the report based on the Status header alone.
    if status == "error" and _has_actionable_findings(report_text):
        status = "partial"

    summary_lines = [
        f"# Dispatch result — {specialty}",
        f"**Status:** {status}",
        f"**Cost:** ${cost_usd:.4f}",
        f"**Turns:** {turns_consumed}",
        f"**Outcome:** {outcome or 'unknown'}",
    ]
    if status == "partial":
        summary_lines.append(
            "**Note:** Subprocess exited non-zero but the specialist produced "
            "findings. READ THE REPORT BODY BELOW before deciding next action."
        )
    if error_msg:
        summary_lines.append(f"**Error:** {error_msg}")
    summary_lines.append("")
    summary_lines.append("---")
    summary_lines.append("")
    summary_lines.append("## Specialist's report")
    summary_lines.append("")
    summary_lines.append(report_text)

    # ── Mandatory next-action reminder (per spec D3) ─────────────────
    # The hypothesis tree is the engagement plan. Update it now, not later.
    # This block lands at the bottom of the tool result so it's the freshest
    # context for the manager's next decision.
    required_action_lines = [
        "",
        "---",
        "",
        "## REQUIRED next action",
        "",
    ]
    if hypothesis_id is not None:
        required_action_lines.extend([
            f"Call `kb_update_hypothesis(id={hypothesis_id}, status=...,",
            f"evidence_refs=[...])` BEFORE issuing any other tool call.",
            f"Choose status based on the specialist's report above:",
            f"  - `confirmed`: outcome explicitly says 'CONFIRMED'",
            f"  - `refuted`: outcome explicitly says 'REFUTED'",
            f"  - `inconclusive`: outcome 'INCONCLUSIVE' or Status was 'partial'",
            f"  - `abandoned`: you've decided not to pursue this hypothesis further",
            "",
            f"Then count: how many dispatches have you made against hypothesis "
            f"#{hypothesis_id}? If 2 or more, apply the Two-failure pivot rule "
            f"(propose 3 orthogonal hypotheses before dispatching again).",
        ])
    else:
        required_action_lines.extend([
            "This dispatch was not tied to a hypothesis (hypothesis_id was None).",
            "Either:",
            "  - Call `kb_add_hypothesis(...)` NOW to record what you learned",
            "    from the dispatch, OR",
            "  - Call `kb_add_note(target=..., body='[dispatch] ...')` to",
            "    document the exploratory result without committing to a hypothesis.",
        ])
    summary_lines.extend(required_action_lines)

    return format_tool_result("\n".join(summary_lines))


TOOLS.append(dispatch_specialist)
