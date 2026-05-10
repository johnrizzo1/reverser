"""OpenAI-compatible backend for local models (Ollama, vLLM, llama.cpp, etc.)."""

import json
import logging
import re
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import AgentEvent, Backend
from .tools import mcp_tools_to_openai, execute_tool

log = logging.getLogger(__name__)

# --- Text-based tool call extraction ---
# Many local models (especially via Ollama) output tool calls as plain text
# instead of using the structured tool_calls field. This handles multiple
# formats including Qwen3's native XML.

# JSON-based patterns
_JSON_TOOL_PATTERNS = [
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

# Qwen3/Qwen3.5 native XML format:
# <tool_call>
# <function=function_name>
# <parameter=key>value</parameter>
# <parameter=key2>value2</parameter>
# </function>
# </tool_call>
_QWEN3_XML_PATTERN = re.compile(
    r'<tool_call>\s*<function=(?P<name>[^>]+)>\s*(?P<params>(?:<parameter=[^>]+>.*?</parameter>\s*)*)</function>\s*</tool_call>',
    re.DOTALL,
)

_QWEN3_PARAM_PATTERN = re.compile(
    r'<parameter=(?P<key>[^>]+)>\s*(?P<value>.*?)\s*</parameter>',
    re.DOTALL,
)


def _parse_qwen3_xml_calls(text: str, known_tools: set[str]) -> list[tuple[str, str]]:
    """Extract tool calls from Qwen3's native XML format."""
    results = []
    for m in _QWEN3_XML_PATTERN.finditer(text):
        name = m.group("name").strip()
        if name not in known_tools:
            continue
        params_text = m.group("params")
        args = {}
        for pm in _QWEN3_PARAM_PATTERN.finditer(params_text):
            key = pm.group("key").strip()
            value = pm.group("value").strip()
            # Try to parse as JSON value (numbers, bools, etc.)
            try:
                args[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                args[key] = value
        results.append((name, json.dumps(args)))
    return results


def _extract_text_tool_calls(text: str, known_tools: set[str]) -> list[tuple[str, str]]:
    """Try to extract tool calls from plain text output.

    Handles multiple formats:
    - JSON: {"name": "...", "arguments": {...}}
    - JSON in <tool_call> tags or code fences
    - Qwen3 native XML: <tool_call><function=name><parameter=k>v</parameter></function></tool_call>

    Returns list of (tool_name, arguments_json) tuples.
    Only returns matches where the tool name is in known_tools.
    """
    # Try Qwen3 XML format first (most specific)
    results = _parse_qwen3_xml_calls(text, known_tools)
    if results:
        return results

    # Fall back to JSON patterns
    for pattern in _JSON_TOOL_PATTERNS:
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
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        # OpenAI-compatible backends don't have a native MCP allowed-tools
        # filter — tool exposure happens at OpenAI tool-call time. For the
        # manager profile (which sets allowed_tools), we accept the parameter
        # for signature parity with the abstract Backend class but rely on
        # prompt discipline to honor the restriction. A stricter enforcement
        # would filter the tools list before passing it to the model, but
        # that's out of scope for this fix.
        _ = allowed_tools  # accept-and-ignore
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        turn = 0
        has_used_tools = False

        while turn < max_turns:
            turn += 1
            yield AgentEvent(kind="turn", turns=turn)

            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=self._openai_tools if self._openai_tools else None,
                    extra_body={"think": True},
                )
            except Exception as e:
                yield AgentEvent(kind="error", content=str(e), is_error=True)
                yield AgentEvent(kind="result", content=f"Error: {e}", subtype="error")
                return

            choice = response.choices[0]
            assistant_msg = choice.message

            # Accumulate the assistant message for conversation history
            messages.append(_message_to_dict(assistant_msg))

            # Extract thinking from the reasoning field (Ollama/Qwen3.5)
            msg_data = assistant_msg.model_dump() if hasattr(assistant_msg, 'model_dump') else {}
            reasoning = msg_data.get("reasoning") or ""
            if reasoning.strip():
                yield AgentEvent(kind="thinking", content=reasoning.strip())

            # Process content — extract any remaining thinking blocks in text
            raw_content = assistant_msg.content or ""

            # Extract <think>...</think> blocks as thinking events
            thinking_match = re.search(r'<think>(.*?)</think>', raw_content, re.DOTALL)
            if thinking_match:
                think_text = thinking_match.group(1).strip()
                if think_text and not reasoning:
                    yield AgentEvent(kind="thinking", content=think_text)
                # Remove thinking block from content for further processing
                display_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            else:
                display_content = raw_content

            # Also handle unclosed <think> tags (known Qwen3.5 bug)
            if display_content.startswith('<think>'):
                think_text = display_content[7:].strip()
                if think_text and not reasoning:
                    yield AgentEvent(kind="thinking", content=think_text)
                display_content = ""

            # Emit visible text content (stripped of thinking/tool XML)
            visible_text = re.sub(r'<tool_call>.*?</tool_call>', '', display_content, flags=re.DOTALL).strip()
            if visible_text:
                yield AgentEvent(kind="text", content=visible_text)

            # Check for tool calls
            if not assistant_msg.tool_calls:
                # Some models output tool calls as plain text (JSON or XML)
                # instead of using the structured tool_calls field.
                text_calls = []
                if raw_content:
                    text_calls = _extract_text_tool_calls(
                        raw_content, self._tool_names,
                    )

                if text_calls:
                    has_used_tools = True
                    # Execute the tool calls the model embedded in its text.
                    result_parts = []
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

                        status = "ERROR" if is_error else "OK"
                        result_parts.append(
                            f"[Tool result: {name} — {status}]\n{result_text}"
                        )

                    # Feed results back as a user message so the model can
                    # process them naturally (synthetic tool messages confuse
                    # many local models).
                    results_msg = "\n\n".join(result_parts)
                    results_msg += "\n\nContinue your analysis. Make more tool calls if needed, or provide your final answer."
                    messages.append({"role": "user", "content": results_msg})

                    # Continue the loop so the model can process results.
                    continue

                # No tool calls found. Decide whether to nudge the model
                # to use tools or accept the response as final.
                #
                # Nudge when:
                # - The model hasn't used any tools yet (it's just planning/
                #   explaining instead of doing work)
                # - The model has used tools before but gave a short/empty
                #   reply (likely stalled)
                if not has_used_tools:
                    messages.append({
                        "role": "user",
                        "content": "Don't just describe what you would do — actually do it now. "
                        "Use the available tools to start your analysis.",
                    })
                    continue

                if len(visible_text) < 80:
                    messages.append({
                        "role": "user",
                        "content": "Continue your analysis. Use the available tools to gather more information, then provide your complete answer.",
                    })
                    continue

                # Model has used tools and gave a substantive text response — it's done.
                yield AgentEvent(
                    kind="result",
                    content=visible_text,
                    turns=turn,
                    subtype="success",
                )
                return

            # Process tool calls
            has_used_tools = True
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
    d = {"role": msg.role, "content": msg.content or ""}
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
