"""Agent orchestration — runs the Claude-powered RE agent."""

import json
import sys

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    UserMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from .prompts import (
    SYSTEM_PROMPT,
    TRIAGE_PROMPT_TEMPLATE,
    ANALYZE_PROMPT_TEMPLATE,
    SOLVE_PROMPT_TEMPLATE,
)
from .tools import create_re_mcp_server
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


def _format_tool_input(input_dict: dict) -> str:
    """Format tool input compactly."""
    try:
        return json.dumps(input_dict, indent=2)
    except (TypeError, ValueError):
        return str(input_dict)


def _truncate(text: str, max_lines: int = 30) -> str:
    """Truncate text to max_lines for display."""
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


def _extract_tool_result_text(block: ToolResultBlock) -> str:
    """Extract plain text from a ToolResultBlock."""
    content = block.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


async def run_agent(
    binary_path: str,
    mode: str = "analyze",
    budget: float = 2.0,
    verbosity: int = 0,
    log_path: str | None = None,
) -> str | None:
    """Run the RE agent on a binary.

    Args:
        binary_path: Path to the binary to analyze.
        mode: One of 'triage', 'analyze', 'solve'.
        budget: Max USD to spend on API calls.
        verbosity: 0=text only, 1=+tool calls/results, 2=+thinking.
        log_path: Path for the session log. Auto-generated if None.

    Returns:
        The agent's final result text, or None if stopped.
    """
    server = create_re_mcp_server()

    template = PROMPT_TEMPLATES.get(mode, ANALYZE_PROMPT_TEMPLATE)
    prompt = template.format(binary_path=binary_path)

    if log_path is None:
        log_path = session_log_path(binary_path)

    slog = SessionLog(log_path)
    slog.log_session_start(binary_path, mode, budget)
    print(f"[Session log: {log_path}]", file=sys.stderr)

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"re": server},
        allowed_tools=["mcp__re__*"],
        permission_mode="bypassPermissions",
        max_turns=50,
        max_budget_usd=budget,
    )

    result_text = None
    turn = 0

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                turn += 1
                slog.log_turn(turn)
                _log(verbosity, 1, f"\n{DIM}── turn {turn} ──{RESET}")

                for block in message.content:
                    if isinstance(block, ThinkingBlock):
                        slog.log_thinking(block.thinking)
                        _log(verbosity, 2, f"\n{MAGENTA}{BOLD}[thinking]{RESET}")
                        _log(verbosity, 2, f"{MAGENTA}{_truncate(block.thinking, 50)}{RESET}")

                    elif isinstance(block, ToolUseBlock):
                        slog.log_tool_call(block.name, block.input)
                        _log(verbosity, 1, f"\n{CYAN}{BOLD}[tool] {block.name}{RESET}")
                        _log(verbosity, 1, f"{CYAN}{_format_tool_input(block.input)}{RESET}")

                    elif isinstance(block, TextBlock):
                        slog.log_text(block.text)
                        print(block.text, end="", flush=True)

            elif isinstance(message, UserMessage):
                if isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            text = _extract_tool_result_text(block)
                            slog.log_tool_result(text, is_error=bool(block.is_error))

                            if verbosity >= 1:
                                is_err = block.is_error
                                color = RED if is_err else GREEN
                                label = "[error]" if is_err else "[result]"
                                _log(verbosity, 1, f"{color}{label}{RESET}")
                                _log(verbosity, 1, f"{DIM}{_truncate(text)}{RESET}")

            elif isinstance(message, ResultMessage):
                cost = getattr(message, "total_cost_usd", None)
                turns = getattr(message, "num_turns", None)

                if message.subtype == "success":
                    result_text = message.result
                else:
                    print(f"\n[Agent stopped: {message.subtype}]", file=sys.stderr)

                slog.log_session_end(result_text, cost, turns, message.subtype)

                if cost:
                    print(f"\n[Cost: ${cost:.4f}]", file=sys.stderr)
                if turns:
                    print(f"[Turns: {turns}]", file=sys.stderr)
    finally:
        slog.close()
        print(f"[Log saved: {log_path}]", file=sys.stderr)

    return result_text
