"""Base types for the agent backend abstraction."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class AgentEvent:
    """An event emitted by an agent backend during execution."""
    kind: str           # text, thinking, tool_call, tool_result, turn, result, error
    content: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_use_id: str = ""        # present on tool_call/tool_result events
    turn: int = 0                # 1-based; 0 means "not associated with a turn"
    is_error: bool = False
    cost: float | None = None
    turns: int | None = None
    subtype: str = ""   # for result events: success, max_turns, budget, error


class Backend(ABC):
    """Abstract base for LLM backends that can run the RE agent."""

    @abstractmethod
    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the agent with the given prompt and yield events.

        Args:
            prompt: The user message / task description.
            system_prompt: System prompt including profile additions.
            max_turns: Maximum agent turns (tool-call loops).
            max_budget_usd: Budget cap (only enforced by Claude backend).
            allowed_tools: Explicit allow-list of MCP tool names. None means
                "all tools" (the default wildcard `mcp__re__*`).

        Yields:
            AgentEvent instances as the agent works.
        """
        ...
