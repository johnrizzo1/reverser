"""Agent session manager — runs the agent and streams events to the TUI."""

from dataclasses import dataclass
from pathlib import Path

from ..prompts import SYSTEM_PROMPT, WEB_SYSTEM_PROMPT  # noqa: F401
from ..profiles import Profile
from ..tools import ALL_TOOLS
from ..tools._common import is_url
from ..backends import AgentEvent, create_backend
from ..session_log import SessionLog, session_log_path

# Profiles that operate on web targets rather than binary files
_WEB_PROFILES = {"webpentest", "webapi", "webrecon"}


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
        return self.profile_key in _WEB_PROFILES


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
    ):
        self._is_web = profile.key in _WEB_PROFILES
        self._is_url_target = is_url(binary_path) if binary_path else False

        if binary_path and not self._is_url_target:
            self.target = str(Path(binary_path).resolve())
        else:
            self.target = binary_path  # URL or empty

        # Keep binary_path for backward compat
        self.binary_path = self.target

        self.profile = profile
        self.budget = budget
        self.max_turns = max_turns
        self.findings: list[str] = []
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

        if log_path is None:
            log_path = session_log_path(binary_path, is_url=self._is_url_target)
        self._log_path = log_path
        self._slog = SessionLog(log_path)
        self._slog.log_session_start(
            self.target, f"interactive/{profile.key}", budget,
        )

    @property
    def log_path(self) -> str:
        return self._log_path

    @property
    def is_running(self) -> bool:
        return self._running

    def _build_system_prompt(self) -> str:
        if self._is_web or self._is_url_target:
            base = WEB_SYSTEM_PROMPT.format(budget=self.budget, max_turns=self.max_turns)
        else:
            base = SYSTEM_PROMPT.format(budget=self.budget, max_turns=self.max_turns)

        addendum = self.profile.system_addendum
        if addendum:
            base += "\n" + addendum

        if self._is_web or self._is_url_target:
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

    def _build_prompt(self, user_message: str) -> str:
        """Build the prompt including conversation context."""
        parts = []

        if self.findings:
            parts.append("## Previous findings from this session\n")
            for i, finding in enumerate(self.findings[-8:], 1):
                parts.append(f"### Exchange {i}\n{finding}\n")
            parts.append("---\n")

        parts.append(user_message)

        if self._is_web or self._is_url_target:
            parts.append(f"\n\n[IMPORTANT: The target is exactly: {self.target} — use this for all tool calls]")
        else:
            parts.append(f"\n\n[IMPORTANT: The binary path is exactly: {self.target} — use this path for all tool calls]")
        return "\n".join(parts)

    def cancel(self):
        """Request cancellation of the current query."""
        self._cancel = True

    async def send(self, user_message: str):
        """Send a message to the agent and yield AgentEvents."""
        self._running = True
        self._cancel = False

        prompt = self._build_prompt(user_message)
        system_prompt = self._build_system_prompt()

        remaining_budget = max(0.1, self.budget - self.stats.total_cost)

        turn_text_parts = []

        try:
            async for event in self._backend.run(
                prompt=prompt,
                system_prompt=system_prompt,
                max_turns=self.max_turns,
                max_budget_usd=remaining_budget,
            ):
                if self._cancel:
                    break

                # Log events
                if event.kind == "turn":
                    self.stats.turns += 1
                    self._slog.log_turn(self.stats.turns)
                    yield AgentEvent(kind="turn", turns=self.stats.turns)

                elif event.kind == "thinking":
                    self._slog.log_thinking(event.content)
                    yield event

                elif event.kind == "tool_call":
                    self._slog.log_tool_call(event.tool_name, event.tool_input)
                    yield event

                elif event.kind == "tool_result":
                    self._slog.log_tool_result(event.content, is_error=event.is_error)
                    yield event

                elif event.kind == "text":
                    self._slog.log_text(event.content)
                    turn_text_parts.append(event.content)
                    yield event

                elif event.kind == "result":
                    if event.cost:
                        self.stats.total_cost += event.cost
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
            self._running = False

        # Store findings for context in future turns
        full_text = "".join(turn_text_parts)
        if full_text.strip():
            summary = full_text[:2000]
            if len(full_text) > 2000:
                summary += "\n[... truncated]"
            self.findings.append(f"User: {user_message}\n\nAgent: {summary}")

    def close(self):
        self._slog.close()
