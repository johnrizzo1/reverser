"""Test doubles for the gui_service tests.

FakeBackend implements the Backend ABC and yields a scripted sequence of
AgentEvents. The script can be replaced per-test via .script = [...].
"""
from collections.abc import AsyncIterator

from reverser.backends.base import AgentEvent, Backend


class FakeBackend(Backend):
    def __init__(self, *args, **kwargs) -> None:
        # Accept the constructor signature the real backends use; ignore.
        self.script: list[AgentEvent] = [
            AgentEvent(kind="turn", turns=1),
            AgentEvent(kind="text", content="Hello from the fake backend."),
            AgentEvent(kind="result", subtype="success", cost=0.01, turns=1),
        ]
        self.calls: list[dict] = []  # records run() calls for assertions

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "max_turns": max_turns,
            "max_budget_usd": max_budget_usd,
            "allowed_tools": allowed_tools,
        })
        for event in self.script:
            yield event
