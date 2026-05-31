"""Claude backend — wraps claude_agent_sdk for the agent loop."""

import json
from collections.abc import AsyncIterator

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
    create_sdk_mcp_server,
)

from .base import AgentEvent, Backend


def _rate_limit_status(message):
    """Return 'allowed'|'allowed_warning'|'rejected' if this is a rate-limit
    event, else None. Duck-typed so it needs no SDK constructor."""
    return getattr(getattr(message, "rate_limit_info", None), "status", None)


class ClaudeBackend(Backend):
    """Backend that uses the Claude Agent SDK."""

    def __init__(self, tools: list):
        self._tools = tools

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        server = create_sdk_mcp_server(
            name="re",
            version="0.1.0",
            tools=self._tools,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"re": server},
            allowed_tools=allowed_tools or ["mcp__re__*"],
            permission_mode="bypassPermissions",
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
        )

        turn = 0

        try:
            async for message in query(prompt=prompt, options=options):
                rl_status = _rate_limit_status(message)
                if rl_status is not None:
                    if rl_status == "rejected":
                        yield AgentEvent(
                            kind="error",
                            content="Claude API rate limit/quota rejected the request; "
                                    "cannot continue this turn.",
                            subtype="quota_exhausted",
                            is_error=True,
                        )
                        yield AgentEvent(kind="result", content="Error: quota_exhausted",
                                         subtype="error")
                        return
                    if rl_status == "allowed_warning":
                        yield AgentEvent(kind="llm_status",
                                         content="Approaching Claude API rate limit.")
                    continue
                if isinstance(message, AssistantMessage):
                    turn += 1
                    yield AgentEvent(kind="turn", turns=turn, turn=turn)

                    for block in message.content:
                        if isinstance(block, ThinkingBlock):
                            yield AgentEvent(kind="thinking", content=block.thinking, turn=turn)

                        elif isinstance(block, ToolUseBlock):
                            try:
                                input_str = json.dumps(block.input, indent=2)
                            except (TypeError, ValueError):
                                input_str = str(block.input)
                            yield AgentEvent(
                                kind="tool_call",
                                tool_name=block.name,
                                tool_input=input_str,
                                tool_use_id=block.id,
                                turn=turn,
                            )

                        elif isinstance(block, TextBlock):
                            yield AgentEvent(kind="text", content=block.text, turn=turn)

                elif isinstance(message, UserMessage):
                    if isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, ToolResultBlock):
                                text = _extract_result_text(block)
                                yield AgentEvent(
                                    kind="tool_result",
                                    content=text,
                                    tool_use_id=getattr(block, "tool_use_id", ""),
                                    is_error=bool(block.is_error),
                                    turn=turn,
                                )

                elif isinstance(message, ResultMessage):
                    cost = getattr(message, "total_cost_usd", None)
                    turns = getattr(message, "num_turns", None)
                    result_text = ""

                    if message.subtype == "success":
                        result_text = message.result or ""
                    else:
                        result_text = f"Agent stopped: {message.subtype}"

                    yield AgentEvent(
                        kind="result",
                        content=result_text,
                        cost=cost,
                        turns=turns,
                        subtype=message.subtype,
                    )
        except Exception as e:
            yield AgentEvent(kind="error", content=f"Claude backend error: {e}",
                             subtype="api_error", is_error=True)
            yield AgentEvent(kind="result", content=f"Error: {e}", subtype="error")
            return


def _extract_result_text(block: ToolResultBlock) -> str:
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
