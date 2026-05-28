"""Agent session manager — runs the agent and streams events to the TUI."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .prompts import (
    OBJECTIVE_ALIGNMENT_PROMPT,
    PROFILE_OPERATING_CONTRACT,
    SYSTEM_PROMPT,
    WEB_SYSTEM_PROMPT,
    NETWORK_SYSTEM_PROMPT,
)  # noqa: F401
from .profiles import Profile, is_web_profile
from .tools import ALL_TOOLS
from .tools._common import is_url
from .backends import AgentEvent, create_backend
from .session_log import SessionLog, session_log_path

# Imported lazily in from_target to avoid a circular-import at module level
# (targets.py imports sessions.py → agent_session.py would be a cycle).
# from .targets import Target, Address  # ← NOT a top-level import

def _is_http_url(target: str) -> bool:
    return target.startswith(("http://", "https://")) if target else False


def _now_iso_session() -> str:
    """ISO-8601 UTC timestamp at second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Exchange:
    """One agent ↔ user round-trip during a session.

    Stored on Session.exchanges (replaces the old findings list of strings).
    Captures the per-turn cost so the snapshot can persist it for resume.
    """
    user: str
    agent: str
    turn: int
    timestamp: str   # ISO-8601
    cost: float      # USD spent on this exchange
    # Structured per-turn events captured during this exchange. Used by
    # _build_prompt to give the resumed LLM a faithful record of what
    # tools ran and what they returned, instead of only the agent's
    # truncated text.
    events: list[dict] = field(default_factory=list)


@dataclass
class TurnStats:
    """Running statistics for the current session."""
    turns: int = 0
    total_cost: float = 0.0
    budget: float = 5.0
    max_turns: int = 50
    binary_path: str = ""
    target: str = ""
    profile_key: str = "general"

    @property
    def is_web(self) -> bool:
        return is_web_profile(self.profile_key)


class AgentSession:
    """Manages an interactive agent session with conversation context."""

    def __init__(
        self,
        binary_path: str,
        profile: Profile,
        budget: float = 5.0,
        max_turns: int = 50,
        log_path: str | None = None,
        backend_name: str = "claude",
        model: str | None = None,
        api_base: str | None = None,
        resume_from: "SessionSnapshot | None" = None,
        session_id: str | None = None,
    ):
        # Per-turn callbacks set by the host to bridge sub-agent events:
        #   on_dispatch_event(specialty, dispatch_id, sub_turn, kind, content)
        #   on_kb_event(kind, payload)   # kind in {"hypothesis", "finding"}
        # None means "do not surface" — used by CLI-only contexts.
        self.on_dispatch_event = None
        self.on_kb_event = None

        if resume_from is not None:
            self._init_resumed(resume_from, profile, backend_name, model, api_base)
        else:
            self._init_new(
                binary_path=binary_path,
                profile=profile,
                budget=budget,
                max_turns=max_turns,
                log_path=log_path,
                backend_name=backend_name,
                model=model,
                api_base=api_base,
                session_id=session_id,
            )
        # Make this session reachable to session-aware tools (e.g. dispatch_specialist)
        from .sessions import current_session
        current_session.set(self)

    def emit_dispatch_event(
        self,
        specialty: str,
        dispatch_id: str,
        sub_turn: int,
        kind: str,
        content: str,
    ) -> None:
        """Surface a dispatch_specialist sub-agent event to the host."""
        cb = self.on_dispatch_event
        if cb is None:
            return
        try:
            cb(specialty, dispatch_id, sub_turn, kind, content)
        except Exception:
            pass

    def emit_kb_event(self, kind: str, payload: dict) -> None:
        """Surface a KB write to the host.

        `kind` is "hypothesis" or "finding"; payload matches the WS frame
        body without the `type` field, i.e. {"action": ..., "row": ...}.
        """
        cb = self.on_kb_event
        if cb is None:
            return
        try:
            cb(kind, payload)
        except Exception:
            pass

    @classmethod
    def from_target(cls, target: "Target", **kwargs) -> "AgentSession":  # type: ignore[name-defined]
        """Construct an AgentSession from a Target, pinning the current primary address.

        The primary address value is used as binary_path so the existing init path
        works unchanged. After construction, target_obj and active_address are set
        to carry the rich Target/Address objects for callers that need them, and the
        snapshot is back-patched with the logical identity fields and re-persisted.

        NOTE: self.target (the legacy string field) is set to the primary address
        value by _init_new. self.target_obj holds the full Target object.
        """
        from .sessions import save as save_snapshot
        primary = target.primary_address
        inst = cls(binary_path=primary.value, **kwargs)
        inst.target_obj = target
        inst.active_address = primary
        # Back-patch the snapshot with logical-identity fields now that target_obj
        # is known (they were empty when _init_new minted the snapshot).
        inst._snapshot.target_name = target.name
        inst._snapshot.active_address_id = primary.id
        save_snapshot(inst._snapshot)
        return inst

    def _init_new(
        self, *, binary_path, profile, budget, max_turns,
        log_path, backend_name, model, api_base, session_id=None,
    ):
        """Original __init__ body — fresh session."""
        from .sessions import (
            new_snapshot, save as save_snapshot, SessionConfig,
        )

        # Legacy object references — set to None here; populated only via from_target.
        self.target_obj = None   # type: Optional["Target"]  # noqa: F821
        self.active_address = None  # type: Optional["Address"]  # noqa: F821

        self._is_web = is_web_profile(profile.key)
        self._is_network = profile.domain == "network"
        self._is_url_target = is_url(binary_path) if binary_path else False
        self._is_web_target = _is_http_url(binary_path)
        self._is_network_target = self._is_url_target and not self._is_web_target

        if (
            binary_path
            and not self._is_url_target
            and not self._is_web
            and not self._is_network
        ):
            self.target = str(Path(binary_path).resolve())
        else:
            self.target = binary_path  # URL, network host, or empty

        # Keep binary_path for backward compat
        self.binary_path = self.target

        self.profile = profile
        self.budget = budget
        self.max_turns = max_turns
        self.exchanges: list[Exchange] = []
        self.stats = TurnStats(
            budget=budget,
            max_turns=max_turns,
            binary_path=self.target,
            target=self.target,
            profile_key=profile.key,
        )
        self._backend = create_backend(
            backend_name,
            ALL_TOOLS,
            model=model,
            api_base=api_base,
        )
        self._backend_name = backend_name
        self._running = False
        self._cancel = False
        self._stop_requested = False
        # Consumed (once) by send() to inject a KB-consult kickoff into
        # the first prompt of the session. True for both fresh starts
        # and resumes — the KB is per-target so even a fresh session
        # may have prior data worth recalling.
        self._first_send_pending = True

        if log_path is None:
            log_path = session_log_path(binary_path, is_url=self._is_url_target)
        self._log_path = log_path
        self._slog = SessionLog(log_path)
        self._slog.log_session_start(
            self.target, f"interactive/{profile.key}", budget,
        )

        # Mint and persist the initial snapshot
        config = SessionConfig(
            profile=profile.key,
            backend=backend_name,
            model=model,
            api_base=api_base,
            budget=budget,
            max_turns=max_turns,
        )
        self._snapshot = new_snapshot(
            target=self.target,
            log_path=self._log_path,
            config=config,
            session_id=session_id,
            target_name=self.target_obj.name if self.target_obj is not None else "",
            active_address_id=self.active_address.id if self.active_address is not None else "",
        )
        save_snapshot(self._snapshot)

    def _init_resumed(
        self, snap: "SessionSnapshot", profile, backend_name, model, api_base,
    ):
        """Restore session state from a snapshot."""
        import os
        from .sessions import save as save_snapshot, ConversationEntry

        # Legacy object references — not populated on the resume path (legacy sessions
        # do not carry a full Target object). from_target is the only path that sets them.
        self.target_obj = None
        self.active_address = None

        if snap.config.profile != profile.key:
            raise ValueError(
                f"Cannot resume: snapshot profile is {snap.config.profile!r} "
                f"but caller passed profile {profile.key!r}. Drop the -p flag "
                f"to use the snapshot's profile, or start a new session."
            )

        # Resolve target / web flags from the snapshot
        self.target = snap.target
        self.binary_path = self.target
        self._is_url_target = is_url(self.target) if self.target else False
        self._is_web = is_web_profile(profile.key)
        self._is_network = profile.domain == "network"
        self._is_web_target = _is_http_url(self.target)
        self._is_network_target = self._is_url_target and not self._is_web_target

        self.profile = profile
        self.budget = snap.config.budget
        self.max_turns = snap.config.max_turns

        # Restore stats
        self.stats = TurnStats(
            budget=snap.config.budget,
            max_turns=snap.config.max_turns,
            binary_path=self.target,
            target=self.target,
            profile_key=profile.key,
            total_cost=snap.stats.total_cost,
            turns=snap.stats.turns,
        )

        # Restore exchanges from conversation entries
        self.exchanges = [
            Exchange(
                user=e.user, agent=e.agent, turn=e.turn,
                timestamp=e.timestamp, cost=e.cost,
            )
            for e in snap.conversation
        ]

        # Backend (use snapshot's backend config unless overridden)
        effective_backend = backend_name or snap.config.backend
        effective_model = model if model is not None else snap.config.model
        effective_api_base = api_base if api_base is not None else snap.config.api_base
        self._backend = create_backend(
            effective_backend,
            ALL_TOOLS,
            model=effective_model,
            api_base=effective_api_base,
        )
        self._backend_name = effective_backend
        self._running = False
        self._cancel = False
        self._stop_requested = False
        # First send after this resume gets the kickoff hint (which
        # additionally mentions "resuming" + prior turn count when
        # snap.stats.turns > 0). See _build_prompt.
        self._first_send_pending = True

        # Reuse the existing log
        self._log_path = snap.log_path
        self._slog = SessionLog(self._log_path)

        # Take ownership: mark snapshot active with our pid
        snap.state = "active"
        snap.pid = os.getpid()
        self._snapshot = snap
        save_snapshot(self._snapshot)

        # Audit log: record the resume
        try:
            self._slog.log_session_resumed(
                session_id=snap.session_id,
                prior_turn=snap.stats.turns,
                prior_cost=snap.stats.total_cost,
            )
        except Exception:
            pass

    @property
    def log_path(self) -> str:
        return self._log_path

    @property
    def is_running(self) -> bool:
        return self._running

    def _build_system_prompt(self) -> str:
        # Profile-based classification takes precedence over target-shape
        # heuristics: a manager/pentest/ad/exploit session with an IP target
        # must get NETWORK_SYSTEM_PROMPT even though is_url() returns True
        # for dotted addresses.
        if self._is_web or (self._is_web_target and not self._is_network):
            base = WEB_SYSTEM_PROMPT.format(budget=self.budget, max_turns=self.max_turns)
        elif self._is_network or self._is_network_target:
            base = NETWORK_SYSTEM_PROMPT.format(budget=self.budget, max_turns=self.max_turns)
        else:
            base = SYSTEM_PROMPT.format(budget=self.budget, max_turns=self.max_turns)

        if "## Objective Alignment" not in base:
            base += "\n" + OBJECTIVE_ALIGNMENT_PROMPT
        base += "\n" + PROFILE_OPERATING_CONTRACT

        addendum = self.profile.system_addendum
        if addendum:
            base += "\n" + addendum

        if self._is_web or (self._is_web_target and not self._is_network):
            base += f"""

## Interactive Mode

You are in interactive mode. The user will send you messages and you should respond helpfully.

**CRITICAL: The target being tested is:**
**{self.target}**

You MUST use this target for ALL tool calls. Every tool that takes a `target` or `url` \
argument MUST use: {self.target}
Do NOT scan or test any domains/hosts outside the target scope without user confirmation.

You have access to all web penetration testing tools. Use them when the user asks you to \
investigate the target. Be conversational but efficient — use tools proactively when \
they would help answer the user's question.

You also have access to the `bash` tool for running arbitrary commands, and `write_file` \
and `read_file` for filesystem operations.

**IMPORTANT: Complete all your analysis before giving your final response.** Keep making \
tool calls until you have gathered enough information to fully answer the user's request. \
Only give your text response after all tool calls are done.

When the user asks you to write a report, document findings, or save output to a file, \
you MUST use the `write_file` tool to create the file.

When you respond, present your findings clearly with relevant details.
"""
        elif self._is_network or self._is_network_target:
            base += f"""

## Interactive Mode

You are in interactive mode. The user will send you messages and you should respond helpfully.

**CRITICAL: The target being engaged is a network host/service:**
**{self.target}**

This is NOT a file path. Do not pass it to disassemblers, decompilers, or any binary
analysis tool. Pass it as the `target` argument to network tools (nmap_scan, dns_recon,
whatweb_scan, etc.) and to `dispatch_specialist` when delegating.

Follow the methodology in your profile addendum. Honor the scope envelope in `scope.toml`.

**IMPORTANT: Complete all your analysis before giving your final response.** Keep making
tool calls until you have gathered enough information to fully answer the user's request.
Only give your text response after all tool calls are done.

When the user asks you to write a report, document findings, or save output to a file,
you MUST use the `write_file` tool to create the file.
"""
        elif os.path.isdir(self.target):
            # Multi-file target (e.g. extracted zip archive)
            file_listing = "\n".join(
                f"  - {self.target}/{entry}"
                for entry in sorted(os.listdir(self.target))
                if os.path.isfile(os.path.join(self.target, entry))
            )
            base += f"""

## Interactive Mode

You are in interactive mode. The user will send you messages and you should respond helpfully.

**CRITICAL: The target directory containing the challenge files is:**
**{self.target}**

The directory contains the following files:
{file_listing}

You MUST analyze ALL files in the target directory — they are likely related (e.g. a VM
binary + bytecode, an executable + config, etc.). Use the full path to each file in tool calls.

You have access to all reverse engineering tools. Use them when the user asks you to
investigate the files. Be conversational but efficient — use tools proactively when
they would help answer the user's question.

**IMPORTANT: Complete all your analysis before giving your final response.** Keep making
tool calls until you have gathered enough information to fully answer the user's request.
Do NOT stop after just a few tool calls — continue investigating until you have a
comprehensive answer. Only give your text response after all tool calls are done.

When the user asks you to write a report, document findings, or save output to a file,
you MUST use the `write_file` tool to create the file. Do NOT just print the content —
actually write it to disk. If the user asks for a markdown file, write a .md file.

When you respond, present your findings clearly with relevant details.
"""
        else:
            base += f"""

## Interactive Mode

You are in interactive mode. The user will send you messages and you should respond helpfully.

**CRITICAL: The binary being analyzed is located at this exact path:**
**{self.target}**

You MUST use this exact path for ALL tool calls. Do NOT guess, invent, or modify the path.
Every tool that takes a `path` argument MUST receive exactly: {self.target}

You have access to all reverse engineering tools. Use them when the user asks you to
investigate the binary. Be conversational but efficient — use tools proactively when
they would help answer the user's question.

**IMPORTANT: Complete all your analysis before giving your final response.** Keep making
tool calls until you have gathered enough information to fully answer the user's request.
Do NOT stop after just a few tool calls — continue investigating until you have a
comprehensive answer. Only give your text response after all tool calls are done.

When the user asks you to write a report, document findings, or save output to a file,
you MUST use the `write_file` tool to create the file. Do NOT just print the content —
actually write it to disk. If the user asks for a markdown file, write a .md file.

When you respond, present your findings clearly with relevant details.
"""
        return base

    def _recent_findings_strings(self, limit: int = 8) -> list[str]:
        """Project recent exchanges into the legacy "findings" string format.

        The original prompt builder used a list of "User: X\\n\\nAgent: Y"
        strings. This helper reproduces that format from the new structured
        Exchange storage so prompt behavior is preserved.
        """
        return [
            f"User: {e.user}\n\nAgent: {e.agent}"
            for e in self.exchanges[-limit:]
        ]

    def _render_exchange_history(self, limit: int = 8) -> str:
        """Render the last `limit` exchanges as a structured history block.

        Includes tool calls and tool results when the exchange carries
        `events` (post schema bump), otherwise falls back to the
        truncated agent-text summary for backward compat with old
        snapshots. Thinking events are dropped — too noisy for the
        prompt — but tool I/O is preserved so the resumed LLM doesn't
        have to rediscover state from scratch.
        """
        out: list[str] = []
        recent = self.exchanges[-limit:]
        for i, e in enumerate(recent, 1):
            out.append(f"### Exchange {i} (turn {e.turn})")
            out.append(f"User: {e.user}")
            if e.events:
                for ev in e.events[-60:]:  # per-exchange cap keeps prompt sane
                    kind = ev.get("kind")
                    if kind == "tool_call":
                        out.append(
                            f"[tool {ev.get('name', '?')}] {ev.get('args', '')[:300]}"
                        )
                    elif kind == "tool_result":
                        marker = "OK" if ev.get("ok") else "ERR"
                        out.append(f"[{marker}] {ev.get('content', '')[:500]}")
                    elif kind == "text":
                        out.append(f"Agent: {ev.get('content', '')[:500]}")
                    # `thinking` is intentionally dropped.
            else:
                out.append(f"Agent: {e.agent}")
            out.append("")
        return "\n".join(out)

    def _recent_findings_strings(self, limit: int = 8) -> list[str]:
        """Legacy projection of exchanges into "User: X\\n\\nAgent: Y"
        strings. Retained for tests that exercise the old shape; the
        live prompt builder uses _render_exchange_history instead.
        """
        return [
            f"User: {e.user}\n\nAgent: {e.agent}"
            for e in self.exchanges[-limit:]
        ]

    def _build_prompt(self, user_message: str) -> str:
        """Build the prompt including conversation context."""
        parts = []

        if self._first_send_pending:
            prior_turns = (
                self._snapshot.stats.turns
                if getattr(self, "_snapshot", None)
                else 0
            )
            # The KB is per-target, so even a fresh session may have
            # findings/hypotheses/notes from earlier sessions worth
            # recalling. Always tell the agent to check; reword for
            # resume to flag the prior-turn context too.
            if prior_turns > 0:
                parts.append(
                    "## Resuming session\n"
                    f"You are resuming a session that previously ran "
                    f"{prior_turns} turn(s). Before deciding what to do "
                    "next, consult the knowledge base for this target — "
                    "`kb_show`, `kb_list_hypotheses`, `kb_list_services`, "
                    "`kb_list_creds`, `kb_list_hosts` — so you don't "
                    "repeat work you've already done.\n"
                )
            else:
                parts.append(
                    "## Session start\n"
                    "Before deciding what to do, consult the knowledge "
                    "base for prior context on this target — `kb_show`, "
                    "`kb_list_hypotheses`, `kb_list_services`, "
                    "`kb_list_creds`, `kb_list_hosts`. The KB persists "
                    "across sessions, so earlier engagements may have "
                    "left findings, hypotheses, or notes worth reading "
                    "before you start any new work.\n"
                )

        if self.exchanges:
            parts.append("## Previous session activity\n")
            parts.append(self._render_exchange_history())
            parts.append("---\n")

        parts.append(user_message)

        if self._is_web or self._is_web_target:
            parts.append(f"\n\n[IMPORTANT: The target is exactly: {self.target} — use this for all tool calls]")
        elif self._is_network or self._is_network_target:
            parts.append(f"\n\n[IMPORTANT: The target host/service is exactly: {self.target} — pass it as the `target` argument to network tools; do NOT treat it as a file path]")
        elif self._is_url_target:
            parts.append(f"\n\n[IMPORTANT: The target is exactly: {self.target} — use this for all tool calls]")
        elif os.path.isdir(self.target):
            parts.append(f"\n\n[IMPORTANT: The target directory is: {self.target} — analyze ALL files within it]")
        else:
            parts.append(f"\n\n[IMPORTANT: The binary path is exactly: {self.target} — use this path for all tool calls]")
        return "\n".join(parts)

    def cancel(self):
        """Request cancellation of the current query."""
        self._cancel = True

    def stop(self) -> None:
        """User-initiated stop. Marks state stopped and persists.

        Distinct from cancel(): cancel halts a single in-flight query;
        stop signals "I'm done for now, expect to resume." Sets the cancel
        flag too so any in-flight turn unwinds.
        """
        from .sessions import save as save_snapshot
        if self._snapshot.state == "completed":
            # Terminal — don't downgrade
            return
        self._cancel = True
        self._stop_requested = True
        self._snapshot.state = "stopped"
        self._snapshot.stopped_at = _now_iso_session()
        self._snapshot.pid = None
        save_snapshot(self._snapshot)
        try:
            self._slog.log_session_stopped(
                cost=self.stats.total_cost, turns=self.stats.turns,
            )
        except Exception:
            pass

    def mark_completed(self) -> None:
        """Mark session completed (terminal)."""
        from .sessions import save as save_snapshot
        self._snapshot.state = "completed"
        self._snapshot.stopped_at = _now_iso_session()
        self._snapshot.pid = None
        save_snapshot(self._snapshot)

    def update_budget(self, new_budget: float) -> None:
        """Update the budget cap in-place. Preserves conversation history.

        Updates the four touchpoints that read budget: self.budget (used by
        run_turn for remaining-budget calc), self.stats.budget (display),
        self._snapshot.config.budget (resume persistence). The snapshot is
        saved so the change survives a stop/resume cycle.
        """
        from .sessions import save as save_snapshot
        self.budget = float(new_budget)
        self.stats.budget = float(new_budget)
        self._snapshot.config.budget = float(new_budget)
        save_snapshot(self._snapshot)

    def update_max_turns(self, new_max_turns: int) -> None:
        """Update the max-turns cap in-place. Preserves conversation history.

        Updates: self.max_turns (used by run_turn for remaining-turns calc),
        self.stats.max_turns (display), self._snapshot.config.max_turns
        (resume persistence). Snapshot saved.
        """
        from .sessions import save as save_snapshot
        self.max_turns = int(new_max_turns)
        self.stats.max_turns = int(new_max_turns)
        self._snapshot.config.max_turns = int(new_max_turns)
        save_snapshot(self._snapshot)

    def _autosave_snapshot(self) -> None:
        """Update the snapshot with current stats + exchanges and persist.

        Called at the end of each turn. Cheap (snapshot is a few KB JSON).
        """
        from .sessions import save as save_snapshot, ConversationEntry
        self._snapshot.stats.total_cost = self.stats.total_cost
        self._snapshot.stats.turns = self.stats.turns
        self._snapshot.conversation = [
            ConversationEntry(
                user=e.user, agent=e.agent, turn=e.turn,
                timestamp=e.timestamp, cost=e.cost,
                events=list(e.events),
            )
            for e in self.exchanges
        ]
        save_snapshot(self._snapshot)

    async def send(self, user_message: str):
        """Send a message to the agent and yield AgentEvents."""
        self._running = True
        self._cancel = False

        prompt = self._build_prompt(user_message)
        system_prompt = self._build_system_prompt()
        # Kickoff hint is one-shot — consume the flag before the loop so
        # any subsequent send() (this turn or later) sees a normal prompt.
        self._first_send_pending = False

        remaining_budget = max(0.1, self.budget - self.stats.total_cost)

        turn_text_parts: list[str] = []
        turn_events: list[dict] = []
        last_turn_cost: float = 0.0

        try:
            try:
                async for event in self._backend.run(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_turns=self.max_turns,
                    max_budget_usd=remaining_budget,
                    allowed_tools=self.profile.tools_allowlist,
                ):
                    if self._cancel:
                        break

                    # Backends use a per-`run()` local turn counter that
                    # restarts at 1 on every send_message. The renderer
                    # buckets events by `frame.turn`, so without remapping
                    # the second message's events collide with Turn 1/2
                    # from the first message and disappear from view.
                    # Rewrite every non-turn event to carry the cumulative
                    # turn number that `self.stats.turns` tracks — the same
                    # value the yielded "turn" event reports as
                    # `turns=...`. Logs become consistent too.
                    if event.kind != "turn":
                        event.turn = self.stats.turns

                    if event.kind == "turn":
                        self.stats.turns += 1
                        self._slog.log_turn(self.stats.turns)
                        yield AgentEvent(
                            kind="turn",
                            turns=self.stats.turns,
                            turn=self.stats.turns,
                        )

                    elif event.kind == "thinking":
                        self._slog.log_thinking(event.content, turn=event.turn or None)
                        turn_events.append({
                            "kind": "thinking",
                            "content": (event.content or "")[:2000],
                        })
                        yield event

                    elif event.kind == "tool_call":
                        self._slog.log_tool_call(
                            event.tool_name, event.tool_input,
                            turn=event.turn or None,
                            tool_use_id=event.tool_use_id or None,
                        )
                        args_str = (
                            event.tool_input
                            if isinstance(event.tool_input, str)
                            else json.dumps(event.tool_input)
                        )
                        turn_events.append({
                            "kind": "tool_call",
                            "name": event.tool_name or "",
                            "args": (args_str or "")[:2000],
                        })
                        yield event

                    elif event.kind == "tool_result":
                        self._slog.log_tool_result(
                            event.content, is_error=event.is_error,
                            turn=event.turn or None,
                            tool_use_id=event.tool_use_id or None,
                        )
                        turn_events.append({
                            "kind": "tool_result",
                            "ok": not event.is_error,
                            "content": (event.content or "")[:2000],
                        })
                        yield event

                    elif event.kind == "text":
                        self._slog.log_text(event.content, turn=event.turn or None)
                        turn_text_parts.append(event.content)
                        turn_events.append({
                            "kind": "text",
                            "content": (event.content or "")[:2000],
                        })
                        yield event

                    elif event.kind == "result":
                        if event.cost:
                            self.stats.total_cost += event.cost
                            last_turn_cost = float(event.cost)
                        self._slog.log_session_end(
                            event.content if event.subtype == "success" else None,
                            event.cost, event.turns, event.subtype,
                        )
                        yield event

                    elif event.kind == "error":
                        yield event

            except Exception as e:
                yield AgentEvent(kind="error", content=str(e), is_error=True)
        finally:
            # Runs on normal completion, error, cancel, and aclose(). The
            # prior layout had the append+autosave AFTER the try/finally —
            # CancelledError propagated past finally and the partial
            # exchange was lost, which is why a stopped 44-turn session
            # could resume with `conversation: []`.
            self._running = False
            try:
                full_text = "".join(turn_text_parts)
                if full_text.strip() or turn_events:
                    summary = full_text[:2000]
                    if len(full_text) > 2000:
                        summary += "\n[... truncated]"
                    self.exchanges.append(Exchange(
                        user=user_message,
                        agent=summary,
                        turn=self.stats.turns,
                        timestamp=_now_iso_session(),
                        cost=last_turn_cost,
                        events=turn_events,
                    ))
            except Exception:
                pass  # never block the cancel/stop path
            try:
                self._autosave_snapshot()
            except Exception:
                pass

    def close(self):
        self._slog.close()
