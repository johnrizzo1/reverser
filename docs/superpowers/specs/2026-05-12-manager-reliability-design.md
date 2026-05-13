# Manager profile reliability follow-up — design

**Date:** 2026-05-12
**Status:** Design approved; ready for implementation plan
**Source:** Post-mortem analysis of 83-turn / 2h49m engagement against
  `10.129.60.148` (Hack The Box "facts" — Camaleon CMS). No foothold
  achieved. See post-mortem summary in conversation history; key failure
  modes are reproduced in §1.
**Predecessor specs:** Manager profile (2026-05-09), Hypothesis-driven
  prompts (2026-05-12), Playwright integration (2026-05-12).

---

## 1. Goal

Close six concrete failure modes observed in the post-mortem of a real
engagement. Together they make the manager profile reliably orchestrate
engagements — especially for local models like `qwen3.6-35b-a3b-ud-mlx`
that have weaker instruction-following than Claude.

The six items share a theme: **make discipline enforceable at the
tool/code level rather than relying on the system prompt alone.**

### Observed failure modes (the bug bar)

From the post-mortem:

1. **Hypothesis discipline ignored at manager level.** Agent created
   1 hypothesis (manager profile mandates 3), made 0 `kb_update_hypothesis`
   calls despite 2 dispatches.
2. **No K-failure pivot at manager level.** Agent retried CSRF/admin-login
   brute force 25× on the same hypothesis without proposing orthogonal
   alternatives. The hypothesis-driven-prompts spec shipped K=3 / K=5
   pivot rules for `pentest` / `webpentest` profiles — manager itself
   was never updated.
3. **`Status: error` dismissed useful dispatch reports.** Two specialist
   dispatches returned valuable intel (CVE-2024-46987 path traversal,
   Playwright + OCR captcha bypass) but the wrapper labeled them
   `Status: error` because the subprocess exited non-zero. Manager read
   "error" and stopped reading.
4. **30-minute "target unreachable" death loop.** After the HTB VM
   dropped, agent ran `ping`/`nc`/`curl --connect-timeout`/`nmap -Pn`
   in a tight loop with identical failures. Never yielded to the user.
5. **CLI target parsing creates bogus directories.** Real bogus
   directories from real user input:
   - `targets/As is common in real life pentests, you will start the Garfield box with credentials for the following account j.arbuckle /`
   - `targets/http:/10.129.60.148/`, `targets/https:/10.129.60.148/`
   - `targets/10.129.244.0/24/` (CIDR nested)
6. **Tool allowlist not enforced.** Manager allowlist excludes
   `http_request`, but the model called `http_request` 43 times in
   the engagement. The OpenAICompat backend's `_filtered_tools()`
   filters what the model SEES but `execute_tool()` doesn't re-check
   the allowlist before dispatching to the handler.

## 2. Non-goals (v1)

- No new tools, no new profiles.
- No new KB schema — existing `hypotheses` table + `dispatch_count`
  column cover what we need.
- Not auto-migrating bogus target dirs (per D6 / Q4) — warn-only.
- No separate persistent counter table — connection-failure tracking
  stays in-memory, per-process.
- Not changing the pentest/webpentest K values (K=3 / K=5 stay as
  shipped) — only adding K=2 for manager.
- Not changing the existing `Status` states `completed` /
  `budget_exhausted` / `turn_limit` / `error` — only ADDING `partial`.
- Not investigating WHY the model invents tool names outside the
  allowlist (training data leakage vs. text-parser permissiveness) —
  the fix works regardless.
- Not adding handler-wrapping enforcement to the Claude backend —
  Claude's SDK enforcement is sufficient in practice (per D8).

## 3. Architectural decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Six-item bundle** including allowlist enforcement | The allowlist gap is real and small to fix. Discipline relies on `dispatch_specialist` being the path for offensive work; if the manager can call any tool directly, the architecture is fighting itself. |
| D2 | **K=2 for manager dispatches** (vs K=3/K=5 for pentest/webpentest) | Manager dispatches are heavier — each costs $0.30–$0.50 and 10–15 turns of specialist time. K=3 would mean 45 turns invested in one wrong hypothesis. K=2 caps at 30 turns. The post-dispatch mandatory-update reminder (D3) covers the "drift" case without needing a second threshold. |
| D3 | **Mandatory `kb_update_hypothesis` after every dispatch**, surfaced via tool-result-appended block | Local models routinely "forget" multi-step state updates that live in the system prompt. The reminder must be the FRESHEST context for the next decision, not 200 lines earlier in the addendum. Append to the dispatch_specialist tool result text. |
| D4 | **`Status: partial` heuristic = body has any of `### Findings`, `### Suggested follow-up`, `### Hypothesis outcome` with ≥20 chars under it** | The return contract (`_RETURN_CONTRACT` in dispatch.py) already mandates these sections. Checking compliance with the contract we set is well-defined. Heuristic alternatives (length thresholds, regex for IPs/CVEs) are flakier. |
| D5 | **Connection-failure circuit breaker: per-target across-all-tools counter, reset only by user input** | The real failure was cross-tool (nmap → bash:ping → bash:curl → bash:nc → nmap). Per-tool counters would have let each family burn 3 retries (9-12 wasted probes). Per-target catches the pattern in 3 failures total. Reset-on-tool-success is exploitable (agent figures out `kb_show` resets); reset-on-user-input forces the yield. |
| D6 | **Target sanitization is warn-only for existing bogus dirs** (no auto-migration) | KB data in the bogus dirs is small and recoverable. Auto-merging two SQLite databases is fiddly (FK collisions, hypothesis ID overlap) — risk of silent data loss. `--check-targets` flag surfaces them for manual cleanup. `list_all()` filters them out. |
| D7 | **Connection counter resets via `reset_all()` on any user input**, not per-target | Simplest reset strategy. If the user is responding, they've acknowledged the yield; clear all counters and start fresh. Mixing targets in a single message is rare in practice. |
| D8 | **Allowlist enforcement at `execute_tool` level, not handler-wrapping** | One central enforcement point covers both backends. Claude backend uses SDK enforcement which is reliable in practice; only add handler wrapping if Claude starts inventing tool names. |
| D9 | **CLI validation rejects whitespace, newlines, and inputs >120 chars** before they reach `target_key()` | Defense in depth — `target_key()` would scrub these, but a clear error at the CLI is better UX than silently creating `targets/as_is_common_..._j_arbuckle_th1sd4mnc4t_1978/`. |
| D10 | **Required-action block uses literal markdown headers, not RFC2119 keywords alone** | "## REQUIRED next action" as a markdown H2 is more salient in the context window than embedded prose. Pattern matches how the hypothesis-driven-prompts spec ships its "NON-NEGOTIABLE" blocks. |

## 4. Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│  CLI:  reverser i -p manager 10.129.60.148                        │
│        │                                                          │
│        ▼                                                          │
│  cli._validate_target_arg  ←─ REJECT whitespace / >120 chars       │
│        │                                                          │
│        ▼                                                          │
│  sessions.target_key  ←─ SANITIZE: URL→netloc, scrub, clamp 64    │
│        │                                                          │
│        ▼                                                          │
│  TUI: ReverserApp loop                                            │
│   │                                                               │
│   │ on_user_input: _conn_breaker.reset_all()  ──┐                 │
│   │                                              │                │
│   ▼                                              │                │
│  AgentSession.run                                │                │
│   │                                              │                │
│   ▼                                              │                │
│  Backend.run(allowed_tools)                      │                │
│   │                                              │                │
│   ▼                                              │                │
│  query loop → execute_tool(name, args,           │                │
│                              allowed_set) ←──────┘ ENFORCE        │
│   │                                                               │
│   ├── tool: nmap_scan(target='10.129.60.148')                     │
│   │     │                                                         │
│   │     ▼                                                         │
│   │   arun_cmd(cmd, target=...)                                   │
│   │     │  ┌─────────────────────────────────────────┐            │
│   │     ├──┤ _conn_breaker.is_tripped(target)?       │ ←─ EARLY   │
│   │     │  │  YES → return is_error result          │   BAIL      │
│   │     │  └─────────────────────────────────────────┘            │
│   │     │                                                         │
│   │     ▼                                                         │
│   │   subprocess.run via asyncio.to_thread                        │
│   │     │                                                         │
│   │     │  ┌─────────────────────────────────────────┐            │
│   │     ├──┤ _conn_breaker.looks_like_conn_error?    │ ←─ COUNT   │
│   │     │  │  → record_failure(target)              │            │
│   │     │  └─────────────────────────────────────────┘            │
│   │     ▼                                                         │
│   │   return result                                               │
│   │                                                               │
│   ├── tool: dispatch_specialist(specialty=..., hypothesis_id=N)   │
│   │     │                                                         │
│   │     ▼                                                         │
│   │   spawn sub-agent (per existing flow)                         │
│   │     │                                                         │
│   │     ▼                                                         │
│   │   format result:                                              │
│   │     ┌─────────────────────────────────────────┐               │
│   │     │ if status==error AND                    │               │
│   │     │    _has_actionable_findings(report):    │               │
│   │     │    status = "partial"  ←─ PROMOTE       │               │
│   │     └─────────────────────────────────────────┘               │
│   │     ┌─────────────────────────────────────────┐               │
│   │     │ append:                                 │               │
│   │     │   ## REQUIRED next action                │ ←─ MANDATORY  │
│   │     │   Call kb_update_hypothesis(id=N, ...)  │   REMINDER   │
│   │     └─────────────────────────────────────────┘               │
│   │                                                               │
│   ▼                                                               │
│  yield events back to TUI                                         │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ Manager system prompt (NEW SECTIONS):                       │  │
│  │   ### Two-failure pivot rule (NON-NEGOTIABLE) — K=2         │  │
│  │   ### Post-dispatch checklist                               │  │
│  │   ### Connection-failure circuit breaker (in CRITICAL RULES)│  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

## 5. File change set

### Add

| Path | Purpose |
|---|---|
| `src/reverser/tools/_conn_breaker.py` | Per-target connection-failure counter + `looks_like_conn_error` classifier. ~80 lines. |
| `tests/test_manager_discipline.py` | ~12 metadata assertions on manager prompt + dispatch_specialist result format. |
| `tests/test_conn_breaker.py` | ~8 tests of the counter + classifier. |
| `tests/test_target_sanitization.py` | ~10 tests of `target_key`, `_is_canonical_target_name`, `_validate_target_arg`, `list_all` filter. |
| `tests/test_backends_allowlist.py` | ~5 tests of `execute_tool` allowlist enforcement. |

### Modify

| Path | Change |
|---|---|
| `src/reverser/profiles/manager.py` | New sections in `system_addendum`: "Two-failure pivot rule (NON-NEGOTIABLE)", "Post-dispatch checklist", "Connection-failure circuit breaker" (within CRITICAL RULES). Skill prompts: `SKILL_KICKOFF` and `SKILL_PIVOT` get dispatch_count reinforcement. |
| `src/reverser/tools/dispatch.py` | `_has_actionable_findings(report)` helper; promote `error` → `partial` when applicable; append `## REQUIRED next action` block to tool result. |
| `src/reverser/backends/tools.py` | `execute_tool` gains `allowed_set: set[str] \| None = None` param; rejects unknown names with clear error before handler lookup. |
| `src/reverser/backends/claude.py` | Compute bare-name set from `allowed_tools`. (Threading to execute_tool deferred per D8.) |
| `src/reverser/backends/openai_compat.py` | Pass `tool_names` (already computed by `_filtered_tools()`) to `execute_tool` as `allowed_set`. |
| `src/reverser/tools/_common.py` | `run_cmd` / `arun_cmd` gain `target: str \| None = None` keyword. Bail early if breaker tripped; record failure on conn-error output. |
| `src/reverser/sessions.py` | Rewrite `target_key()`: URL→netloc, CIDR→network, scrub special chars, clamp 64, lowercase. Add `_is_canonical_target_name(name)` helper. Filter non-canonical entries from `list_all()`. |
| `src/reverser/cli.py` | New `_validate_target_arg(target)` — reject whitespace, newlines, >120 chars. Called early in `_run_interactive`. New `--check-targets` flag for manual cleanup advisory. |
| `src/reverser/tui/app.py` | `on_user_input` calls `_conn_breaker.reset_all()` at top, before existing logic. |
| `CAPABILITY_ROADMAP.md` | "Recently Shipped" entry for manager-reliability bundle. |

### Does not change
- KB schema (no migrations needed).
- pentest / webpentest / exploit / ad profile prompts (their K=3 / K=5 stay).
- `_common.run_cmd` / `arun_cmd` core behavior (only adds early-bail + post-call check, both gated on `target` arg being non-None).
- Existing tool registrations / `ALL_TOOLS` count (no new tools).
- TUI app structure beyond the one-line input-handler hook.

## 6. Manager prompt additions

Three new sections in `src/reverser/profiles/manager.py`'s `system_addendum`.

### 6.1 Two-failure pivot rule (NON-NEGOTIABLE)

Inserted between "Hypothesis-driven methodology" and "Specialist menu":

```
### Two-failure pivot rule (NON-NEGOTIABLE)

Manager engagements fail when the lead keeps re-dispatching the same hypothesis
without pivoting. The 10.129.60.148 engagement is the cautionary tale — 25
retries of the same primitive across 2h49m, no foothold, no flag.

**After 2 dispatches against the same hypothesis**, you MUST:
1. `kb_update_hypothesis(id=X, status=refuted)` with a one-line reason
   synthesizing both dispatch reports.
2. Stop dispatching against that hypothesis.
3. Propose THREE orthogonal hypotheses via `kb_add_hypothesis`. Orthogonal means:
   different target host, different attack surface (web vs. SSH vs. SMB),
   different exploitation class (creds vs. RCE vs. info-disclosure), or
   different specialist (try `ad` instead of `webpentest` if AD signals appeared).

**What counts as a failed dispatch:**
- Specialist returned `Hypothesis outcome: refuted` or `inconclusive`.
- Specialist exited `budget_exhausted` or `turn_limit` without producing a
  confirmed outcome.
- Specialist exited `error` AND the report body has no actionable findings
  (specifically: no `### Findings`, `### Suggested follow-up`, or
  `### Hypothesis outcome` sections — this is the `Status: partial`
  detection in reverse).

**What does NOT count as a failed dispatch:**
- A dispatch that returned `confirmed` (obviously — that's success).
- A dispatch that returned `Status: partial` with actionable findings — treat
  as "needs follow-up dispatch with the new context", NOT as a failure.
- A dispatch that the manager hasn't yet read fully or updated the hypothesis
  from.

The hypothesis tree IS the engagement plan. Update it. `kb_list_hypotheses`
at the start of every new session shows where you left off. Don't re-derive
things you already disproved.
```

### 6.2 Post-dispatch checklist

Inserted just before existing "Reading the return" section:

```
### Post-dispatch checklist (do these in order, every time)

After `dispatch_specialist` returns, BEFORE any other tool call:

1. Read the FULL "Specialist's report" section, including when Status is
   `error` or `partial`. Status alone is not enough — the body may contain
   actionable findings.
2. Call `kb_update_hypothesis(id=<hypothesis_id>, status=...,
   evidence_refs=[<extracted_facts>])` to record the outcome. This is
   mandatory — the dispatch wrapper will remind you in the tool result.
3. If the outcome was `refuted` or `inconclusive`, count: how many
   dispatches have I made against this hypothesis? If 2, apply the
   Two-failure pivot rule above.
4. Decide your next action based on the report content, not just the status.
```

### 6.3 Connection-failure circuit breaker

Inserted into existing CRITICAL RULES section:

```
**Connection-failure circuit breaker.** If three consecutive tools fail with
connection errors against the same target (ECONNREFUSED, EHOSTUNREACH,
"Connection timeout"), the harness will block further probes against that
target and surface an error like "Target appears down (3 consecutive conn
failures: <timestamps>)". When this happens:

1. STOP immediately. Do not run `ping`, `nmap -Pn`, `curl --connect-timeout`,
   or any other connectivity probes.
2. Write a one-line summary of what's down and what you were trying to do.
3. Yield to the user: "The target appears unreachable. Please confirm the
   VM/box is running, then send any message to resume."

The breaker only resets when the user sends a new message. Cheating with
`kb_show` or other "always-succeeds" probes does not reset it.
```

### 6.4 Skill updates

**`SKILL_KICKOFF` (key `k`)** — append:

```
When you dispatch the first specialist after kickoff, remember the two-failure
pivot rule: track `dispatch_count` per hypothesis, and after 2 failed
dispatches against the same hypothesis_id, mark it refuted and propose
three orthogonal alternatives BEFORE dispatching again.
```

**`SKILL_PIVOT` (key `p`)** — append:

```
A natural trigger for this skill: when you see `dispatch_count >= 2` on any
hypothesis with status still in 'testing', that's a Two-failure pivot
signal. Don't wait for the user to invoke /pivot — fold this into your
per-turn checklist.
```

`SKILL_STATUS`, `SKILL_REPORT`, `SKILL_BUDGET`, `SKILL_WRAP_UP` stay
untouched — they don't directly drive dispatches.

## 7. `dispatch_specialist` reforms

Two changes in `src/reverser/tools/dispatch.py`, both in the result-rendering
section (currently at lines 280-336).

### 7.1 `_has_actionable_findings` helper

```python
import re

_PARTIAL_HEURISTIC_PATTERN = re.compile(
    r"###\s+(Findings|Suggested follow-up|Hypothesis outcome)\s*\n(.+?)"
    r"(?=\n###|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _has_actionable_findings(report: str) -> bool:
    """Return True if the report body contains at least one return-contract
    section with non-trivial content (>=20 chars).

    Used by dispatch_specialist to promote Status: error → Status: partial
    when a subprocess errored but the specialist still produced useful intel.
    Heuristic matches against the section headers from `_RETURN_CONTRACT`:
    Findings, Suggested follow-up, Hypothesis outcome.
    """
    for match in _PARTIAL_HEURISTIC_PATTERN.finditer(report):
        body = match.group(2).strip()
        if len(body) >= 20:
            return True
    return False
```

### 7.2 Status promotion in result rendering

After the existing status-detection logic but before `summary_lines`:

```python
# ── Status: partial promotion (per spec D4) ─────────────────────────
if status == "error" and _has_actionable_findings(report_text):
    status = "partial"
```

### 7.3 Required-action block

Computed after status finalization, appended to `summary_lines`:

```python
# ── Mandatory next-action reminder (per spec D3) ────────────────────
required_action_lines = ["", "---", "", "## REQUIRED next action", ""]
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
        f"  - Call `kb_add_hypothesis(...)` NOW to record what you learned",
        f"    from the dispatch, OR",
        f"  - Call `kb_add_note(target=..., body='[dispatch] ...')` to",
        f"    document the exploratory result without committing to a hypothesis.",
    ])
```

### 7.4 Modified summary rendering

```python
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
# NEW: append the required-action block
summary_lines.extend(required_action_lines)
return format_tool_result("\n".join(summary_lines))
```

## 8. Connection-failure circuit breaker

New module `src/reverser/tools/_conn_breaker.py`:

```python
"""Per-target connection-failure circuit breaker.

Tracks consecutive connection errors against each target across ALL tool
families. After 3 consecutive failures, the breaker is "tripped" — subsequent
tool calls against the same target return an immediate error result instead
of running the tool. The breaker only resets when the user sends a new
message to the agent (signaled by `reset_for_target` or `reset_all`).

See spec 2026-05-12-manager-reliability-design.md §8.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from threading import Lock


_CONN_FAILURE_THRESHOLD = 3
_lock = Lock()
_counters: dict[str, list[str]] = {}  # target → list of ISO timestamps

_CONN_ERROR_RE = re.compile(
    r"connection\s+refused"
    r"|connection\s+timed\s+out"
    r"|connection\s+timeout"
    r"|no\s+route\s+to\s+host"
    r"|network\s+is\s+unreachable"
    r"|host\s+unreachable"
    r"|name\s+or\s+service\s+not\s+known"
    r"|nodename\s+nor\s+servname\s+provided"
    r"|could\s+not\s+resolve\s+host"
    r"|operation\s+timed\s+out"
    r"|ECONNREFUSED|EHOSTUNREACH|ENETUNREACH",
    re.IGNORECASE,
)


def looks_like_conn_error(text: str) -> bool:
    """Return True if the given subprocess output looks like a connection error."""
    if not text:
        return False
    return bool(_CONN_ERROR_RE.search(text))


def record_failure(target: str) -> None:
    """Increment the consecutive-failure counter for `target`."""
    if not target:
        return
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock:
        _counters.setdefault(target, []).append(ts)


def is_tripped(target: str) -> bool:
    """Return True if `target` has met or exceeded the failure threshold."""
    if not target:
        return False
    with _lock:
        return len(_counters.get(target, [])) >= _CONN_FAILURE_THRESHOLD


def failure_summary(target: str) -> dict:
    """Return {count, timestamps} for `target` (for the trip error message)."""
    with _lock:
        ts_list = list(_counters.get(target, []))
    return {"count": len(ts_list), "timestamps": ts_list}


def reset_for_target(target: str) -> None:
    """Clear the counter for `target`. Used by tests; production uses reset_all."""
    if not target:
        return
    with _lock:
        _counters.pop(target, None)


def reset_all() -> None:
    """Clear all counters. Called on user input (the 'yield acknowledged' signal)."""
    with _lock:
        _counters.clear()
```

### 8.1 Integration in `_common.run_cmd` / `arun_cmd`

Both gain a `target: str | None = None` keyword arg:

```python
# src/reverser/tools/_common.py
from . import _conn_breaker


def run_cmd(
    cmd: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    cwd: str | None = None,
    stdin_data: str | None = None,
    target: str | None = None,  # NEW
) -> dict:
    # Bail early if breaker tripped
    if target and _conn_breaker.is_tripped(target):
        summary = _conn_breaker.failure_summary(target)
        return {
            "stdout": "",
            "stderr": (
                f"Connection circuit breaker tripped for target={target!r}: "
                f"{summary['count']} consecutive conn failures "
                f"(latest: {summary['timestamps'][-1]}). "
                f"STOP probing this target. Yield to the user and ask them "
                f"to confirm it's reachable. The breaker resets on user input."
            ),
            "returncode": -1,
            "truncated": False,
            "is_error": True,
        }

    # ... existing subprocess.run logic stays the same ...

    # Record on conn-error output
    if target:
        combined = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
        if result["returncode"] != 0 and _conn_breaker.looks_like_conn_error(combined):
            _conn_breaker.record_failure(target)

    return result


async def arun_cmd(*args, target: str | None = None, **kwargs) -> dict:
    """Async wrapper — threads target through to run_cmd."""
    import asyncio
    return await asyncio.to_thread(
        run_cmd, *args, target=target, **kwargs,
    )
```

### 8.2 Tool integration

Tool handlers that know their target pass `target=` to `run_cmd`/`arun_cmd`:
- `network.py`, `web.py`, `netexec.py`, `enum4linux_ng.py` — already
  take a `target` arg in their tool signatures; pass through to subprocess.
- `metasploit.py`, `web_browser.py` — already use `asyncio.to_thread`
  themselves; integration is in their own `_do()` closures rather than
  via `arun_cmd`. Wire conn-error detection at their handler level:
  ```python
  if _conn_breaker.is_tripped(target):
      return format_error(...)
  ```
  Pre-check before launching browser / starting metasploit work.
- `static.py`, `triage.py`, `dynamic.py`, `exploit.py` — analyze local
  binaries, no network target. Pass `target=None` (no breaker behavior).
- `bash` (catch-all): accepts an optional `target` arg in its tool
  schema. If passed and breaker is tripped, fail early. If not passed,
  no breaker interaction. The agent is instructed (via the new manager
  prompt block) to STOP raw connectivity probes when the breaker trips,
  so missed bash calls are acceptable in practice.

### 8.3 TUI input handler hook

```python
# src/reverser/tui/app.py
@on(Input.Submitted, "#user-input")
async def on_user_input(self, event: Input.Submitted) -> None:
    # Reset conn-failure circuit breakers — user input is the 'yield
    # acknowledged' signal.
    from ..tools import _conn_breaker
    _conn_breaker.reset_all()

    text = event.value.strip()
    # ... rest of existing logic stays the same
```

## 9. Target sanitization

### 9.1 `sessions.target_key()` rewrite

```python
import re
from urllib.parse import urlparse

_CANONICAL_TARGET_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_SCRUB_RE = re.compile(r"[^A-Za-z0-9._:-]+")
_MAX_TARGET_KEY_LEN = 64


def target_key(target: str) -> str:
    """Derive a filesystem-safe directory name from a target identifier.

    Handling, in priority order:
      1. Absolute filesystem path → basename
      2. URL with scheme (http://, https://, ftp://) → netloc only
      3. CIDR (IP/N) → network portion before the slash
      4. Otherwise → scrub non-allowed chars to '_', clamp length

    All results are lowercased; clamped to _MAX_TARGET_KEY_LEN; stripped
    of leading/trailing _.- chars. Raises ValueError on empty input or
    if sanitization reduces to empty.
    """
    if not target or not target.strip():
        raise ValueError("target identifier must be non-empty")

    target = target.strip()

    if os.path.isabs(target):
        target = os.path.basename(target)
    elif target.startswith(("http://", "https://", "ftp://")):
        parsed = urlparse(target)
        if parsed.netloc:
            target = parsed.netloc
    elif "/" in target:
        left, _, _ = target.partition("/")
        if left and re.match(r"^\d+\.\d+\.\d+\.\d+$", left):
            target = left
        else:
            target = _SCRUB_RE.sub("_", target)

    if not _CANONICAL_TARGET_RE.fullmatch(target):
        target = _SCRUB_RE.sub("_", target)

    if len(target) > _MAX_TARGET_KEY_LEN:
        target = target[-_MAX_TARGET_KEY_LEN:]

    target = target.lower().strip("_.-")

    if not target:
        raise ValueError("target identifier reduced to empty string after sanitization")

    return target


def _is_canonical_target_name(name: str) -> bool:
    """Return True if a directory name matches the canonical target-key regex."""
    return bool(_CANONICAL_TARGET_RE.fullmatch(name))
```

### 9.2 `list_all()` filter

```python
def list_all(*, exclude_completed: bool = False) -> list[SessionSnapshot]:
    """... existing docstring ..."""
    root = _targets_root()
    if not root.is_dir():
        return []

    all_snaps: list[SessionSnapshot] = []
    for target_dir in root.iterdir():
        if not target_dir.is_dir():
            continue
        # Skip bogus dirs from prior CLI parsing bugs
        if not _is_canonical_target_name(target_dir.name):
            continue
        all_snaps.extend(
            list_for_target(target_dir.name, exclude_completed=exclude_completed)
        )

    all_snaps.sort(key=lambda s: s.last_active_at, reverse=True)
    return all_snaps
```

### 9.3 CLI validation

```python
# src/reverser/cli.py
def _validate_target_arg(target: str) -> tuple[bool, str | None]:
    """Return (is_valid, error_message)."""
    if not target:
        return True, None

    target = target.strip()

    if len(target) > 120:
        return False, (
            f"Target argument is {len(target)} chars (max 120). "
            "Did you accidentally paste a description or scenario text? "
            "Pass just the IP, hostname, or URL."
        )

    if "\n" in target or "\r" in target:
        return False, (
            "Target argument contains newlines. "
            "Pass a single-line IP, hostname, or URL."
        )

    if " " in target or "\t" in target:
        return False, (
            f"Target argument contains whitespace: {target!r}. "
            "Pass a single token (IP, hostname, or URL — no spaces)."
        )

    return True, None
```

Called early in `_run_interactive`:

```python
target = getattr(args, "target", "") or ""
ok, err = _validate_target_arg(target)
if not ok:
    print(f"Error: {err}", file=sys.stderr)
    sys.exit(2)
```

### 9.4 `--check-targets` advisory command

```python
parser.add_argument(
    "--check-targets",
    action="store_true",
    help="Scan targets/ for non-canonical (bogus) target directories "
         "and print a cleanup recommendation, then exit.",
)


def _run_check_targets():
    from .sessions import _is_canonical_target_name, _targets_root
    root = _targets_root()
    if not root.is_dir():
        print(f"No targets/ directory at {root}.")
        return
    bogus = []
    for entry in root.iterdir():
        if entry.is_dir() and not _is_canonical_target_name(entry.name):
            bogus.append(entry)
    if not bogus:
        print("✓ All target directories have canonical names.")
        return
    print(f"⚠ {len(bogus)} non-canonical target directories detected:\n")
    for b in bogus:
        print(f"  {b}")
    print()
    print("To clean up:")
    for b in bogus:
        print(f"  rm -rf {b!s}")
```

## 10. Allowlist enforcement

### 10.1 `execute_tool` gains an `allowed_set` parameter

```python
# src/reverser/backends/tools.py
async def execute_tool(
    handlers: dict, name: str, arguments: str,
    allowed_set: set[str] | None = None,
) -> tuple[str, bool]:
    """Execute an MCP tool and return (result_text, is_error).

    If allowed_set is provided, tool names outside the set are rejected
    with a clear error message — this enforces profile-level allowlists
    that the model would otherwise bypass via invented tool names or
    text-format tool calls.
    """
    if allowed_set is not None and name not in allowed_set:
        allowed_list = ", ".join(sorted(allowed_set)[:20])
        more = "" if len(allowed_set) <= 20 else f" (and {len(allowed_set) - 20} others)"
        return (
            f"Tool {name!r} is not in this profile's allowlist. "
            f"Use one of: {allowed_list}{more}. "
            f"If the desired operation isn't available directly, dispatch to a "
            f"specialist via dispatch_specialist.",
            True,
        )

    handler = handlers.get(name)
    if handler is None:
        return f"Unknown tool: {name}", True

    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as e:
        return f"Invalid JSON arguments: {e}", True

    try:
        result = await handler(args)
    except Exception as e:
        return f"Tool error: {e}", True

    return extract_tool_result_text(result), result.get("is_error", False)
```

### 10.2 `OpenAICompatBackend` threading

```python
# Around line 213 in openai_compat.py
tools_for_model, tool_names = self._filtered_tools(allowed_tools)
# ... later, in the tool-call execution loop (line 383):
result_text, is_error = await execute_tool(
    self._handlers, fn.name, fn.arguments,
    allowed_set=tool_names if allowed_tools else None,
)
```

The `tool_names` set is already computed by `_filtered_tools()`. We just
thread it through. If `allowed_tools` is None (default — open access),
pass `None` to skip enforcement.

### 10.3 `ClaudeBackend` — out of scope per D8

Claude's SDK enforces `allowed_tools` reliably at the model level
(observed in practice — no Claude engagement has shown the 43×
out-of-allowlist call pattern). Adding handler-wrapping for Claude is
deferred to a follow-up if needed.

## 11. Testing strategy

### `tests/test_manager_discipline.py` (~12 tests)

```python
# Metadata assertions on manager prompt content
def test_manager_addendum_mentions_two_failure_pivot()
def test_manager_addendum_specifies_what_counts_as_failed_dispatch()
def test_manager_addendum_lists_what_does_NOT_count_as_failed()
def test_manager_addendum_mentions_connection_failure_breaker()
def test_skill_kickoff_mentions_dispatch_count()
def test_skill_pivot_mentions_dispatch_count_2()

# dispatch_specialist behavior
def test_dispatch_result_includes_required_action_block_when_hypothesis_id_given()
def test_dispatch_result_includes_required_action_block_when_no_hypothesis()
def test_dispatch_result_promotes_error_to_partial_when_findings_present()
def test_dispatch_result_keeps_error_when_no_actionable_findings()
def test_dispatch_result_partial_includes_note_to_read_body()
def test_has_actionable_findings_recognizes_three_section_headers()
def test_has_actionable_findings_rejects_empty_section_bodies()
```

### `tests/test_conn_breaker.py` (~8 tests)

```python
def test_counter_starts_at_zero()
def test_record_failure_increments()
def test_is_tripped_at_threshold()
def test_below_threshold_not_tripped()
def test_reset_for_target_clears_counter()
def test_reset_all_clears_everything()
def test_looks_like_conn_error_recognizes_common_patterns()  # table-driven
def test_per_target_isolation()
def test_run_cmd_bails_when_tripped()  # mock subprocess.run
def test_run_cmd_records_failure_on_conn_error_output()
```

### `tests/test_target_sanitization.py` (~10 tests)

```python
def test_target_key_strips_http_scheme()
def test_target_key_strips_https_scheme()
def test_target_key_takes_cidr_network_portion()
def test_target_key_scrubs_special_chars()
def test_target_key_clamps_length_at_64()
def test_target_key_lowercases_everything()
def test_target_key_raises_on_empty_input()
def test_is_canonical_target_name_accepts_valid_forms()  # table-driven
def test_is_canonical_target_name_rejects_bogus_forms()
def test_list_all_filters_non_canonical_dirs()
def test_cli_validate_target_arg_rejects_long_inputs()
def test_cli_validate_target_arg_rejects_whitespace()
```

### `tests/test_backends_allowlist.py` (~5 tests)

```python
def test_execute_tool_rejects_name_outside_allowlist()
def test_execute_tool_passes_through_when_no_allowlist()
def test_execute_tool_error_message_lists_allowed_alternatives()
def test_openai_compat_threads_allowlist_to_execute_tool()
def test_claude_backend_computes_bare_name_set()
```

**Total new tests: ~35**. Test count: 580 → 615.

## 12. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | K=2 too aggressive — agent pivots when one more dispatch with extra_context would have worked | K=2 is per-hypothesis-id, not per-dispatch. User can manually call kickoff/dispatch with a new hypothesis_id to "re-roll" with fresh context. |
| R2 | Required-action reminder bloats every dispatch result, costing tokens | ~150 tokens per dispatch. 5-10 dispatches per engagement → 750-1500 token overhead. Worth it for the discipline. |
| R3 | `Status: partial` heuristic false positives | Heuristic requires `### <name>` header AND ≥20 chars under it. Stack traces won't match the structured form. |
| R4 | Connection-failure breaker false positives on intermittent network blips | Counter resets on user input; user can resume by sending any message. Worst case: one "target unreachable" message and a resend. |
| R5 | Breaker doesn't detect when bash runs raw `curl` without target= | Accepted. The prompt steers toward tool-typed calls. If raw bash conn errors happen, the next typed-tool call catches them. |
| R6 | Target sanitization changes case behavior (existing dirs with uppercase letters become inaccessible) | The new lowercasing matches `normalize_target` (KB lowercases everything). Mixed-case dirs were already inconsistent. Filter step in list_all hides them; no data loss on disk. |
| R7 | Allowlist enforcement breaks existing tests | `allowed_set` defaults to None (no enforcement). Existing tests pass through unchanged. |
| R8 | Claude backend's allowlist remains bypassable | Accepted per D8. No production evidence of Claude inventing tool names. Add handler wrapping later if needed. |
| R9 | `--check-targets` flag adds CLI surface that could collide with other flags | Top-level flag (not subcommand-specific). One-time output, exits immediately. Low risk of collision. |

## 13. Rollout

Single-merge, like prior small specs.

| Step | What |
|---|---|
| 1 | Implementation plan written (writing-plans skill). |
| 2 | Implementation per plan — ~14-18 tasks given the 6 items + ~35 tests. |
| 3 | All tests pass (target: 615 = 580 current + 35 new). |
| 4 | Manual smoke: run `reverser i -p manager <target>` against a real target. Verify the dispatch result format shows `## REQUIRED next action`, an out-of-allowlist tool call gets rejected, and `reverser --check-targets` lists the existing bogus dirs. |
| 5 | Merge to main. |

## 14. Future work (v2+)

- **Auto-merge of bogus target dirs.** Per D6, warn-only now. Migration
  utility could be added once we have evidence that real KB data is
  stranded in bogus dirs (current evidence: no).
- **Claude backend allowlist enforcement via handler wrapping.** Per D8,
  only if Claude starts inventing tool names. Adds a small wrapping
  decorator to handlers passed into `create_sdk_mcp_server`.
- **Telemetry on K-failure trips.** Counter / event for "pivot rule
  fired on this hypothesis_id" — would let us tune K empirically based
  on whether agents actually pivot when triggered, or whether they
  ignore the breaker.
- **Connection-failure detection in raw bash commands.** Parse the
  agent's `curl`/`ping`/`nc` command lines to extract target. Adds
  shell-arg parsing complexity; not blocking.
- **Per-target persistent counter survives across resume.** Currently
  in-memory only. Persistent state would let the breaker survive
  process restarts; not clearly an improvement (resume is itself a
  yield signal that should clear the breaker).
