"""Manager-profile dispatch tool: spawn specialist sub-agents via the SDK.

Pure helpers (compose_dispatch_context, parse_dispatch_report) are
unit-tested in isolation. The dispatch_specialist tool itself wraps these
helpers around an SDK Task call (see Task 13).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import json as _json
import os as _os
import re
import threading


# ── Pure helpers ────────────────────────────────────────────────────


_RETURN_CONTRACT = """## Persist as you go (REQUIRED)
BEFORE you write your final report, persist what you found into the knowledge
base by CALLING the tools — do not merely describe it:
- For each finding, call `kb_add_finding` (title, severity, description,
  reproduction, confidence, reachability — attach evidence_paths when you have
  them).
- For each NEW hypothesis you formed, call `kb_add_hypothesis`.
The lead reads the KB, not your prose. A finding you only describe but never
persist may be lost.

## Return contract
When you finish, your final assistant message MUST be a human-readable markdown
report with the sections below. You MAY also append a fenced ```json block
restating it (helpful but optional — the lead parses your markdown either way).

### TL;DR
One sentence.

### Findings
What you discovered. Bullet list.

### Hypothesis outcome
One of: CONFIRMED, REFUTED, INCONCLUSIVE — followed by one-sentence justification.

### KB writes
List what you persisted, ONE item per bullet, using exactly this format so the
lead can reconcile it (this mirrors your kb_add_* calls above):
- Finding: <short title> — <one-line description>
- Hypothesis: <statement>

### Suggested follow-up
What you would test next if you had more budget.

### Machine-readable summary (optional — append last if you include it)
```json
{
  "tldr": "one sentence",
  "findings": ["..."],
  "hypothesis_outcome": "confirmed | refuted | inconclusive",
  "kb_writes": ["..."],
  "follow_up": ["..."],
  "status": "success | partial | error"
}
```
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


# ── Schema-validated dispatch report helpers ────────────────────────

from ..schemas.models import DispatchReportModel  # noqa: E402
from ..schemas.validation import validate_args  # noqa: E402

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """Find the first JSON object that looks like a dispatch report (has 'tldr')."""
    for pat in (_JSON_BLOCK, _BARE_JSON):
        for m in pat.finditer(text or ""):
            try:
                obj = _json.loads(m.group(1))
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and "tldr" in obj:
                return obj
    return None


_MD_OUTCOME = re.compile(
    r"###\s+Hypothesis\s+outcome\s*\n(.+?)(?=\n###|\Z)", re.IGNORECASE | re.DOTALL
)
_OUTCOME_KEYWORDS = {
    "confirmed": "confirmed",
    "refuted": "refuted",
    "inconclusive": "inconclusive",
}


def _outcome_from_markdown(text: str) -> str | None:
    """Extract the outcome from the markdown '### Hypothesis outcome' section.

    Returns a normalized outcome string, or None if the section is absent.
    A present-but-unrecognized section defaults to 'inconclusive'.
    """
    m = _MD_OUTCOME.search(text or "")
    if not m:
        return None
    body = m.group(1).strip().lower()
    if not body:
        return "inconclusive"
    for keyword, value in _OUTCOME_KEYWORDS.items():
        if keyword in body:
            return value
    return "inconclusive"


def parse_dispatch_report(text: str):
    """Return (outcome, model_or_None, error_text_or_None).

    Prefers a validated JSON block (which also yields the structured model);
    falls back to the markdown '### Hypothesis outcome' section so a specialist
    that emits only markdown is still parsed correctly. ``error_text`` is None
    whenever an outcome was derived (JSON or markdown) — a missing JSON block is
    NOT a failure and must not trigger re-running the specialist. Only when
    neither a JSON block nor a markdown outcome section is present do we return a
    non-None error (with the defensive 'inconclusive' default).
    """
    obj = _extract_json(text)
    if obj is not None:
        result = validate_args(DispatchReportModel, obj)
        if result.ok:
            return result.value.hypothesis_outcome.value, result.value, None
    md_outcome = _outcome_from_markdown(text)
    if md_outcome is not None:
        return md_outcome, None, None
    return (
        "inconclusive",
        None,
        "No dispatch outcome found (no JSON block and no '### Hypothesis outcome' section).",
    )


def _promote_status(subprocess_status: str, report_model) -> str:
    """If the subprocess errored but the specialist still produced a structured
    report with actionable content, promote to 'partial' so the lead reads it."""
    if subprocess_status == "error" and report_model is not None and (
        report_model.findings or report_model.follow_up or report_model.kb_writes
    ):
        return "partial"
    return subprocess_status


# ── Backstop: reconcile a specialist's markdown report into the KB ───
# Specialists describe findings/hypotheses in their report's "### KB writes"
# section but do not reliably CALL kb_add_finding/kb_add_hypothesis. The
# contract now mandates those calls (primary path); this parser + reconcile is
# the backstop that persists anything the specialist only described, so report
# findings/hypotheses never silently evaporate.

_KB_WRITES_SECTION = re.compile(
    r"###\s+KB writes\s*\n(.+?)(?=\n###|\Z)", re.IGNORECASE | re.DOTALL
)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
# split a "title — description" / "title - description" bullet at the first
# em-dash or spaced hyphen separator
_TITLE_SPLIT = re.compile(r"\s+[—–-]\s+")


def _norm(s: str) -> str:
    """Normalize for dedup: lowercase, collapse whitespace."""
    return " ".join((s or "").lower().split())


def parse_report_kb_writes(report_text: str):
    """Extract labeled findings/hypotheses from a report's '### KB writes' section.

    Returns ``(findings, hypotheses)`` where findings is a list of
    ``{"title": str, "description": str}`` and hypotheses is a list of statement
    strings. Only bullets explicitly prefixed ``Finding:`` / ``Hypothesis:``
    (optionally bold) are extracted, so prose bullets are ignored.
    """
    findings: list[dict] = []
    hypotheses: list[str] = []
    m = _KB_WRITES_SECTION.search(report_text or "")
    if not m:
        return findings, hypotheses
    for line in m.group(1).splitlines():
        bm = _BULLET.match(line)
        if not bm:
            continue
        item = bm.group(1).replace("**", "").strip()
        low = item.lower()
        if low.startswith("finding:") or low.startswith("finding "):
            text = item.split(":", 1)[1].strip() if ":" in item else item
            if not text:
                continue
            title = (_TITLE_SPLIT.split(text, 1)[0].strip() or text)[:120]
            findings.append({"title": title, "description": text})
        elif low.startswith("hypothesis"):
            text = item.split(":", 1)[1].strip() if ":" in item else item
            if text:
                hypotheses.append(text)
    return findings, hypotheses


def reconcile_report_to_kb(kb, findings, hypotheses, *, specialty: str) -> list[str]:
    """Persist report-described findings/hypotheses that aren't already in the KB.

    Findings are stored UNVALIDATED (via evidence_blocker — no real evidence was
    attached) so they are clearly flagged as needing confirmation. Hypotheses are
    created at 'proposed'. Dedup is by normalized title/statement against the live
    KB (so items the specialist already persisted via the mandate are skipped) and
    within the batch. Never raises — bad items are skipped with a noted reason.
    Returns a list of human-readable action lines for the dispatch envelope.
    """
    from ..schemas.models import FindingModel
    from ..schemas.validation import validate_args
    from ..kb.store import FindingFact
    from ..gui_service.kb_emitter import emit_recorded_finding, emit_hypothesis

    actions: list[str] = []

    existing_titles = {_norm(f.title) for f in kb.get_findings()}
    seen: set[str] = set()
    for fnd in findings or []:
        title = (fnd.get("title") or "").strip()[:120]
        if not title:
            continue
        key = _norm(title)
        if key in existing_titles or key in seen:
            continue
        seen.add(key)
        result = validate_args(FindingModel, {
            "title": title,
            "severity": "info",
            "description": fnd.get("description") or title,
            "reproduction": "(reported via specialist dispatch; reproduction not provided)",
            "confidence": 25,
            "reachability": "unknown",
            "evidence_blocker": f"reported via {specialty} dispatch; evidence not attached",
        })
        if not result.ok:
            reason = (result.error_text or "invalid").splitlines()[-1]
            actions.append(f"skipped finding {title!r}: {reason}")
            continue
        m = result.value
        fact = FindingFact(
            title=m.title, severity=m.severity.value, description=m.description,
            evidence_paths=m.evidence_paths, cvss=m.cvss, reproduction=m.reproduction,
            reachability=m.reachability.value, confidence=m.confidence,
            evidence_blocker=m.evidence_blocker, validated=m.validated,
        )
        fid = kb.record_finding(fact)
        try:
            emit_recorded_finding("create", fid, fact)
        except Exception:
            pass
        actions.append(f"finding #{fid} (unvalidated): {title}")

    existing_hyps = {_norm(h.statement) for h in kb.list_hypotheses()}
    seen_h: set[str] = set()
    for stmt in hypotheses or []:
        stmt = (stmt or "").strip()
        if not stmt:
            continue
        key = _norm(stmt)
        if key in existing_hyps or key in seen_h:
            continue
        seen_h.add(key)
        h = kb.add_hypothesis(
            statement=stmt,
            rationale=f"spawned via {specialty} dispatch",
            confidence=25,
        )
        if h is not None:
            try:
                emit_hypothesis("create", h)
            except Exception:
                pass
            actions.append(f"hypothesis #{h.id} (proposed): {stmt[:80]}")

    return actions


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
from ..backends import create_backend  # noqa: E402
from ..profiles import get_profile  # noqa: E402
from ..kb.store import KB  # noqa: E402
from ..kb.scope import load_scope  # noqa: E402


_LOCAL_BACKEND_NAMES = {"lmstudio", "ollama", "local"}
_LOCAL_DISPATCH_THREAD_LOCK = threading.Lock()


@asynccontextmanager
async def _local_dispatch_slot():
    """Serialize local-model specialist dispatches.

    LM Studio and similar local OpenAI-compatible servers often run one model
    request at a time. If a manager emits multiple dispatch_specialist calls in
    the same turn, this keeps those sub-agent calls from overlapping.
    """
    await asyncio.to_thread(_LOCAL_DISPATCH_THREAD_LOCK.acquire)
    try:
        yield
    finally:
        _LOCAL_DISPATCH_THREAD_LOCK.release()


@asynccontextmanager
async def _unserialized_dispatch_slot():
    yield


class _DispatchStalled(Exception):
    """Raised when a dispatched specialist emits no event within the idle window."""

    def __init__(self, idle_seconds: float):
        self.idle_seconds = idle_seconds
        super().__init__(
            f"specialist produced no event within {idle_seconds}s idle window"
        )


def _dispatch_idle_timeout() -> float:
    """Seconds of sub-agent silence before the stall watchdog aborts a dispatch.

    Default 300s (5 min); override with REVERSER_DISPATCH_IDLE_TIMEOUT. A
    malformed value falls back to the default rather than crashing a dispatch.
    """
    raw = _os.environ.get("REVERSER_DISPATCH_IDLE_TIMEOUT")
    if raw is None:
        return 300.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 300.0


def _dispatch_tool_timeout() -> float:
    """Seconds a single in-flight tool call may run before the watchdog aborts the
    dispatch. Generous (default 1800s / 30 min) so real scans finish, but bounded so a
    hung MCP server or background task can't wedge the session forever. Override with
    REVERSER_DISPATCH_TOOL_TIMEOUT; a malformed value falls back to the default."""
    raw = _os.environ.get("REVERSER_DISPATCH_TOOL_TIMEOUT")
    if raw is None:
        return 1800.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1800.0


async def _aiter_with_idle_timeout(
    agen: AsyncIterator,
    idle_seconds: float,
    *,
    tool_seconds: float | None = None,
    is_tool_pending: Callable[[], bool] | None = None,
) -> AsyncIterator:
    """Yield from ``agen``, raising ``_DispatchStalled`` if any single step idles
    longer than the active window. While a tool call is outstanding
    (``is_tool_pending()`` true and ``tool_seconds`` given), the generous
    ``tool_seconds`` window applies so a legitimately long tool is not aborted;
    otherwise the short ``idle_seconds`` window applies. Best-effort closes the
    underlying iterator on stall so the specialist generator is not leaked."""
    it = agen.__aiter__()
    while True:
        if (
            is_tool_pending is not None
            and tool_seconds is not None
            and is_tool_pending()
        ):
            timeout = tool_seconds
        else:
            timeout = idle_seconds
        try:
            item = await asyncio.wait_for(it.__anext__(), timeout)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            try:
                await asyncio.wait_for(it.aclose(), 30)
            except Exception:
                pass
            raise _DispatchStalled(timeout)
        yield item


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
            updated = kb.get_hypothesis(hypothesis_id)
            if updated is not None:
                from ..gui_service.kb_emitter import emit_hypothesis
                emit_hypothesis("update", updated)

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
    # In-flight tool-call count, read by the stall watchdog so it applies the
    # generous tool window (not the short idle window) while a tool runs. Updated
    # before the sess guard below so it stays accurate even without a session.
    # Tradeoff: if a backend emits a "tool_call" whose matching tool_result/error
    # is never emitted (e.g. an `event.kind == "error"` aborts mid-tool), the count
    # stays >0 and a *subsequent* hang waits the tool window instead of the idle
    # window. Bounded by the tool-timeout ceiling, so it can't hang forever.
    _pending_tools = [0]

    def _emit(kind: str, content: str) -> None:
        if kind == "tool_call":
            _pending_tools[0] += 1
        elif kind in ("tool_result", "tool_error"):
            _pending_tools[0] = max(0, _pending_tools[0] - 1)
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
        payload = _json.dumps({"hypothesis_id": hypothesis_id, "sub_goal": sub_goal})
        try:
            sess._slog.log_dispatch_event(
                specialty, "start", payload, dispatch_id=dispatch_id, sub_turn=0,
            )
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, 0, "start", payload,
            )
        except Exception:
            pass

    def _emit_end(status: str, cost: float, turns_consumed: int) -> None:
        if sess is None:
            return
        payload = _json.dumps({"status": status, "cost": cost, "turns": turns_consumed})
        try:
            sess._slog.log_dispatch_event(
                specialty, "end", payload, dispatch_id=dispatch_id, sub_turn=0,
            )
        except Exception:
            pass
        try:
            sess.emit_dispatch_event(
                specialty, dispatch_id, 0, "end", payload,
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

    cfg = sess._snapshot.config if sess is not None else None
    use_session_backend = cfg is not None and cfg.backend != "claude"

    async def _run_specialist(prompt: str) -> str:
        """Run the specialist ONCE for ``prompt`` and return THIS attempt's
        report text. Accumulates cost/turns/status/error_msg across attempts
        via nonlocal (so the first attempt is identical to prior behavior, and
        repair attempts ADD their cost/turns rather than overwriting)."""
        # report_text is nonlocal so a mid-stream exception still leaves the
        # partial report visible to the caller's except path (matches pre-repair
        # behavior). It is not reset between attempts, so a repair attempt that
        # produces no text retains the prior attempt's report.
        nonlocal status, cost_usd, turns_consumed, error_msg, report_text

        _idle = _dispatch_idle_timeout()
        _tool = _dispatch_tool_timeout()
        _pending = lambda: _pending_tools[0] > 0

        if use_session_backend:
            if cfg.backend in _LOCAL_BACKEND_NAMES:
                _emit("thinking", f"Waiting for local backend slot ({cfg.backend})")
                slot = _local_dispatch_slot()
            else:
                slot = _unserialized_dispatch_slot()

            async with slot:
                if cfg.backend in _LOCAL_BACKEND_NAMES:
                    _emit("thinking", f"Acquired local backend slot ({cfg.backend})")
                backend = create_backend(
                    cfg.backend,
                    ALL_TOOLS,
                    model=cfg.model,
                    api_base=cfg.api_base,
                    token_cost_per_1k=getattr(cfg, "token_cost_per_1k", 0.0),
                )
                async for event in _aiter_with_idle_timeout(
                    backend.run(
                        prompt=prompt,
                        system_prompt=full_system_prompt,
                        max_turns=max_turns,
                        max_budget_usd=budget_usd,
                        allowed_tools=sub_allowed_tools,
                    ),
                    _idle,
                    tool_seconds=_tool,
                    is_tool_pending=_pending,
                ):
                    if event.kind == "turn":
                        _sub_turn[0] = event.turn or event.turns or _sub_turn[0] + 1
                    elif event.kind == "text":
                        report_text = event.content
                        if event.content.strip():
                            _emit("text", event.content)
                    elif event.kind == "thinking":
                        if event.content.strip():
                            _emit("thinking", event.content)
                    elif event.kind == "tool_call":
                        _emit(
                            "tool_call",
                            f"{event.tool_name} "
                            f"{_summarize_tool_input(event.tool_input)}",
                        )
                    elif event.kind == "tool_result":
                        kind = "tool_error" if event.is_error else "tool_result"
                        _emit(kind, _summarize_tool_result(event.content))
                    elif event.kind == "error":
                        status = "error"
                        error_msg = event.content
                    elif event.kind == "result":
                        cost_usd += float(event.cost or 0.0)
                        turns_consumed += int(event.turns or _sub_turn[0] or 0)
                        if event.subtype != "success":
                            subtype_str = str(event.subtype).lower()
                            if "budget" in subtype_str:
                                status = "budget_exhausted"
                            elif "turn" in subtype_str or "max_turn" in subtype_str:
                                status = "turn_limit"
                            else:
                                status = "error"
                            if not report_text:
                                report_text = event.content or (
                                    f"(specialist did not produce a report; "
                                    f"subtype={event.subtype})"
                                )
                        elif event.content and not report_text:
                            report_text = event.content
        else:
            async for message in _aiter_with_idle_timeout(
                query(prompt=prompt, options=options),
                _idle,
                tool_seconds=_tool,
                is_tool_pending=_pending,
            ):
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
                    cost_usd += float(getattr(message, "total_cost_usd", 0.0) or 0.0)
                    turns_consumed += int(getattr(message, "num_turns", 0) or 0)
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
        return report_text

    outcome = "inconclusive"
    report_model = None
    reconcile_actions: list[str] = []

    _emit_start()
    try:
        # Run the specialist ONCE. parse_dispatch_report recovers the outcome
        # from the JSON block if present, else from the markdown report — so a
        # missing JSON block is not a failure and we never re-run the specialist
        # (re-running tripled dispatch time without reliably producing JSON).
        report_text = await _run_specialist(sub_goal)
        outcome, report_model, _parse_errors = parse_dispatch_report(report_text)

        # ── Status: partial promotion (per spec D4) ──────────────────
        # If subprocess errored but the validated JSON report carries
        # actionable content, promote status so the manager doesn't dismiss
        # the report based on the Status header alone.
        status = _promote_status(status, report_model)

        # ── Backstop: reconcile the report's KB-writes into the KB ───
        # Specialists describe findings/hypotheses but don't always CALL the
        # kb_add_* tools. Parse the report's "### KB writes" bullets and persist
        # anything not already present (deduped against what the specialist did
        # persist), so report findings never silently evaporate.
        try:
            rec_findings, rec_hyps = parse_report_kb_writes(report_text)
            reconcile_actions = reconcile_report_to_kb(
                kb, rec_findings, rec_hyps, specialty=specialty
            )
        except Exception:
            reconcile_actions = []
    except _DispatchStalled as e:
        # _aiter_with_idle_timeout raises this when a wrapped specialist
        # generator (query/backend.run) emits no event within the idle window.
        status = "timeout"
        error_msg = (
            f"specialist produced no output for {e.idle_seconds:g}s — "
            f"aborted by stall watchdog"
        )
        if not report_text:
            report_text = f"(dispatch aborted: {error_msg})"
        outcome = "inconclusive"
        report_model = None
    except Exception as e:
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"
        if not report_text:
            report_text = f"(dispatch failed: {error_msg})"
        outcome = "inconclusive"
        report_model = None
    finally:
        _emit_end(status, cost_usd, turns_consumed)
        if sess is not None:
            sess._snapshot.in_flight = None
            try:
                save_snapshot(sess._snapshot)
            except Exception:
                pass

    summary_lines = [
        f"# Dispatch result — {specialty}",
        f"**Status:** {status}",
        f"**Cost:** ${cost_usd:.4f}",
        f"**Turns:** {turns_consumed}",
        f"**Outcome:** {outcome or 'unknown'}",
    ]
    if status in ("partial", "timeout"):
        summary_lines.append(
            "**Note:** Subprocess exited non-zero but the specialist produced "
            "findings. READ THE REPORT BODY BELOW before deciding next action."
        )
    if error_msg:
        summary_lines.append(f"**Error:** {error_msg}")
    if reconcile_actions:
        summary_lines.append("")
        summary_lines.append("**Reconciled to KB** (backstop — items the specialist reported):")
        for action in reconcile_actions:
            summary_lines.append(f"- {action}")
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
