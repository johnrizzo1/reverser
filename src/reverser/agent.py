"""Agent orchestration — runs the Claude-powered RE agent."""

import sys

from .prompts import (
    SYSTEM_PROMPT,
    TRIAGE_PROMPT_TEMPLATE,
    ANALYZE_PROMPT_TEMPLATE,
    SOLVE_PROMPT_TEMPLATE,
)
from .tools import ALL_TOOLS
from .backends import AgentEvent, create_backend
from .session_log import SessionLog, session_log_path

# ANSI colors
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BOLD = "\033[1m"

PROMPT_TEMPLATES = {
    "triage": TRIAGE_PROMPT_TEMPLATE,
    "analyze": ANALYZE_PROMPT_TEMPLATE,
    "solve": SOLVE_PROMPT_TEMPLATE,
}


def _log(verbosity: int, min_level: int, msg: str, **kwargs):
    """Print msg to stderr if verbosity >= min_level."""
    if verbosity >= min_level:
        print(msg, file=sys.stderr, **kwargs)


def _truncate(text: str, max_lines: int = 30) -> str:
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


async def run_agent(
    binary_path: str,
    mode: str = "analyze",
    budget: float = 2.0,
    verbosity: int = 0,
    log_path: str | None = None,
    backend_name: str = "claude",
    model: str | None = None,
    api_base: str | None = None,
) -> str | None:
    """Run the RE agent on a binary.

    Args:
        binary_path: Path to the binary to analyze.
        mode: One of 'triage', 'analyze', 'solve'.
        budget: Max USD to spend on API calls.
        verbosity: 0=text only, 1=+tool calls/results, 2=+thinking.
        log_path: Path for the session log. Auto-generated if None.
        backend_name: Backend to use ('claude', 'ollama', etc.).
        model: Model name for non-claude backends.
        api_base: API base URL override.

    Returns:
        The agent's final result text, or None if stopped.
    """
    max_turns = 50

    template = PROMPT_TEMPLATES.get(mode, ANALYZE_PROMPT_TEMPLATE)
    prompt = template.format(binary_path=binary_path)
    system_prompt = SYSTEM_PROMPT.format(budget=budget, max_turns=max_turns)

    if log_path is None:
        log_path = session_log_path(binary_path)

    slog = SessionLog(log_path)
    slog.log_session_start(binary_path, mode, budget)
    print(f"[Session log: {log_path}]", file=sys.stderr)
    if backend_name != "claude":
        print(f"[Backend: {backend_name} / {model}]", file=sys.stderr)

    backend = create_backend(
        backend_name,
        ALL_TOOLS,
        model=model,
        api_base=api_base,
    )

    result_text = None

    try:
        async for event in backend.run(
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_usd=budget,
        ):
            if event.kind == "turn":
                slog.log_turn(event.turns)
                _log(verbosity, 1, f"\n{DIM}── turn {event.turns} ──{RESET}")

            elif event.kind == "thinking":
                slog.log_thinking(event.content)
                _log(verbosity, 2, f"\n{MAGENTA}{BOLD}[thinking]{RESET}")
                _log(verbosity, 2, f"{MAGENTA}{_truncate(event.content, 50)}{RESET}")

            elif event.kind == "tool_call":
                slog.log_tool_call(event.tool_name, event.tool_input)
                _log(verbosity, 1, f"\n{CYAN}{BOLD}[tool] {event.tool_name}{RESET}")
                _log(verbosity, 1, f"{CYAN}{event.tool_input}{RESET}")

            elif event.kind == "tool_result":
                slog.log_tool_result(event.content, is_error=event.is_error)
                if verbosity >= 1:
                    color = RED if event.is_error else GREEN
                    label = "[error]" if event.is_error else "[result]"
                    _log(verbosity, 1, f"{color}{label}{RESET}")
                    _log(verbosity, 1, f"{DIM}{_truncate(event.content)}{RESET}")

            elif event.kind == "text":
                slog.log_text(event.content)
                print(event.content, end="", flush=True)

            elif event.kind == "result":
                if event.subtype == "success":
                    result_text = event.content
                else:
                    print(f"\n[Agent stopped: {event.subtype}]", file=sys.stderr)

                slog.log_session_end(
                    result_text, event.cost, event.turns, event.subtype,
                )

                if event.cost:
                    print(f"\n[Cost: ${event.cost:.4f}]", file=sys.stderr)
                if event.turns:
                    print(f"[Turns: {event.turns}]", file=sys.stderr)

            elif event.kind == "error":
                print(f"\n[Error: {event.content}]", file=sys.stderr)

    finally:
        slog.close()
        print(f"[Log saved: {log_path}]", file=sys.stderr)

    if result_text is None:
        sys.exit(1)

    return result_text
