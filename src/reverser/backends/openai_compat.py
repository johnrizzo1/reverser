"""OpenAI-compatible backend for local models (Ollama, vLLM, llama.cpp, etc.)."""

import json
import logging
import re
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import AgentEvent, Backend
from .tools import mcp_tools_to_openai, execute_tool

log = logging.getLogger(__name__)

# Patterns for detecting tool calls embedded in text output.
# Many local models output tool calls as JSON text instead of using the
# structured tool_calls field.
_TOOL_CALL_PATTERNS = [
    # {"name": "...", "arguments": {...}}
    re.compile(
        r'\{\s*"name"\s*:\s*"(?P<name>[^"]+)"\s*,\s*"arguments"\s*:\s*(?P<args>\{[^}]*\})\s*\}',
        re.DOTALL,
    ),
    # <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    re.compile(
        r'<tool_call>\s*\{\s*"name"\s*:\s*"(?P<name>[^"]+)"\s*,\s*"arguments"\s*:\s*(?P<args>\{.*?\})\s*\}\s*</tool_call>',
        re.DOTALL,
    ),
    # ```json\n{"name": "...", "arguments": {...}}\n```
    re.compile(
        r'```(?:json)?\s*\{\s*"name"\s*:\s*"(?P<name>[^"]+)"\s*,\s*"arguments"\s*:\s*(?P<args>\{.*?\})\s*\}\s*```',
        re.DOTALL,
    ),
]


def _extract_text_tool_calls(text: str, known_tools: set[str]) -> list[tuple[str, str]]:
    """Try to extract tool calls from plain text output.

    Returns list of (tool_name, arguments_json) tuples.
    Only returns matches where the tool name is in known_tools.
    """
    results = []
    for pattern in _TOOL_CALL_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group("name")
            args = m.group("args")
            if name in known_tools:
                try:
                    json.loads(args)  # validate JSON
                    results.append((name, args))
                except json.JSONDecodeError:
                    continue
    return results


class OpenAICompatBackend(Backend):
    """Backend that uses an OpenAI-compatible API (Ollama, vLLM, etc.)."""

    def __init__(
        self,
        tools: list,
        model: str,
        api_base: str = "http://localhost:11434/v1",
        api_key: str = "not-needed",
    ):
        self._model = model
        self._openai_tools, self._handlers = mcp_tools_to_openai(tools)
        self._tool_names = set(self._handlers.keys())
        self._client = AsyncOpenAI(
            base_url=api_base,
            api_key=api_key,
        )

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
    ) -> AsyncIterator[AgentEvent]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        turn = 0

        while turn < max_turns:
            turn += 1
            yield AgentEvent(kind="turn", turns=turn)

            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=self._openai_tools if self._openai_tools else None,
                )
            except Exception as e:
                yield AgentEvent(kind="error", content=str(e), is_error=True)
                yield AgentEvent(kind="result", content=f"Error: {e}", subtype="error")
                return

            choice = response.choices[0]
            assistant_msg = choice.message

            # Accumulate the assistant message for conversation history
            messages.append(_message_to_dict(assistant_msg))

            # Emit any text content
            if assistant_msg.content:
                yield AgentEvent(kind="text", content=assistant_msg.content)

            # Check for tool calls
            if not assistant_msg.tool_calls:
                # Some models output tool calls as plain text JSON instead of
                # using the structured tool_calls field. Try to parse them.
                text_calls = []
                if assistant_msg.content:
                    text_calls = _extract_text_tool_calls(
                        assistant_msg.content, self._tool_names,
                    )

                if text_calls:
                    # Remove the original text-only assistant message — we'll
                    # replace it with proper tool call messages.
                    messages.pop()

                    for name, args in text_calls:
                        yield AgentEvent(
                            kind="tool_call",
                            tool_name=name,
                            tool_input=args,
                        )

                        result_text, is_error = await execute_tool(
                            self._handlers, name, args,
                        )

                        yield AgentEvent(
                            kind="tool_result",
                            content=result_text,
                            is_error=is_error,
                        )

                        # Append as a synthetic assistant + tool message pair
                        # so the model sees the result in conversation history.
                        tool_call_id = f"synthetic_{turn}_{name}"
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": tool_call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": args},
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_text,
                        })

                    # Continue the loop so the model can process results.
                    continue

                # No tool calls at all — the model is done
                yield AgentEvent(
                    kind="result",
                    content=assistant_msg.content or "",
                    turns=turn,
                    subtype="success",
                )
                return

            # Process tool calls
            for tc in assistant_msg.tool_calls:
                fn = tc.function
                yield AgentEvent(
                    kind="tool_call",
                    tool_name=fn.name,
                    tool_input=fn.arguments,
                )

                result_text, is_error = await execute_tool(
                    self._handlers, fn.name, fn.arguments,
                )

                yield AgentEvent(
                    kind="tool_result",
                    content=result_text,
                    is_error=is_error,
                )

                # Append tool result to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

            # If the model hit the finish_reason stop after tool calls,
            # continue the loop so it can process the results.
            if choice.finish_reason == "stop":
                # Model said stop but also made tool calls — shouldn't happen,
                # but handle gracefully by continuing.
                pass

        # Exhausted max_turns
        yield AgentEvent(
            kind="result",
            content="Reached maximum turn limit.",
            turns=turn,
            subtype="max_turns",
        )


def _message_to_dict(msg) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a plain dict for the messages list."""
    d = {"role": msg.role}
    if msg.content:
        d["content"] = msg.content
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d
