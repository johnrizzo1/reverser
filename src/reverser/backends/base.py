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
    ) -> AsyncIterator[AgentEvent]:
        """Run the agent with the given prompt and yield events.

        Args:
            prompt: The user message / task description.
            system_prompt: System prompt including profile additions.
            max_turns: Maximum agent turns (tool-call loops).
            max_budget_usd: Budget cap (only enforced by Claude backend).

        Yields:
            AgentEvent instances as the agent works.
        """
        ...
