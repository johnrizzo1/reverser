"""OpenAI-compatible backend for local models (Ollama, vLLM, llama.cpp, etc.)."""

import json
import logging
import re
import time
import uuid as _uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

from openai import AsyncOpenAI

from .base import AgentEvent, Backend
from .tools import mcp_tools_to_openai, execute_tool

log = logging.getLogger(__name__)

_STATUS_EMIT_INTERVAL_SEC = 0.5

_STREAM_SUPPRESS_MARKERS = (
    "<think",
    "<tool_call",
    "<|tool_call",
    "<|channel>thought",
    "```",
)

# --- Text-based tool call extraction ---
# Many local models (especially via Ollama) output tool calls as plain text
# instead of using the structured tool_calls field. This handles multiple
# formats including Qwen3's native XML.

# JSON-based patterns. Ordered most-specific first so we don't double-match:
# a wrapped <tool_call>{...}</tool_call> would otherwise also be matched by
# the bare-JSON pattern, executing the call twice. The extractor stops at
# the first pattern that yields any matches.
_JSON_TOOL_PATTERNS = [
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
    # {"name": "...", "arguments": {...}}  (least specific, last resort)
    re.compile(
        r'\{\s*"name"\s*:\s*"(?P<name>[^"]+)"\s*,\s*"arguments"\s*:\s*(?P<args>\{[^}]*\})\s*\}',
        re.DOTALL,
    ),
]

# Scrubber for ```json ... ``` fences that wrap tool-call JSON. Matches
# both a single {"name": ..., "arguments": ...} object and a JSON array
# of such objects (which is what DeepSeek-Coder-V2 emits when it batches
# parallel calls). The `[^`]*?` body forbids backticks inside the fence
# so we never match across multiple fences. Only fences whose body
# contains the "name"/"arguments" shape are stripped — unrelated JSON in
# code blocks is preserved.
_TOOL_CALL_FENCE_SCRUB = re.compile(
    r'```(?:json)?\s*[^`]*?"name"\s*:\s*"[^"]+"[^`]*?"arguments"[^`]*?```',
    re.DOTALL,
)


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

# Gemma 4 native format:
# <|tool_call>call:function_name{key:<|"|>value<|"|>}<tool_call|>
_GEMMA_TOOL_CALL_PATTERN = re.compile(
    r'<\|tool_call>call:(?P<name>[a-zA-Z_]\w*)\{(?P<body>.*?)\}<tool_call\|>',
    re.DOTALL,
)

_GEMMA_PARAM_PATTERN = re.compile(
    r'(?P<key>[a-zA-Z_]\w*):<\|"\|>(?P<value>.*?)<\|"\|>',
    re.DOTALL,
)

# Gemma 4 thinking format:
# <|channel>thought\n...\n<channel|>
_GEMMA_THINKING_PATTERN = re.compile(
    r'<\|channel>thought\s*\n(?P<content>.*?)<channel\|>',
    re.DOTALL,
)


def _is_deepseek_family(model: str | None) -> bool:
    """True for any DeepSeek model name (covers Coder-V2, V2, V2.5, R1).

    Detection is by substring so that LM Studio's full GGUF paths
    (e.g. ``lmstudio-community/DeepSeek-Coder-V2-Lite-Instruct-GGUF``)
    match as well as bare tags.
    """
    return "deepseek" in (model or "").lower()


def _build_deepseek_tools_preamble(openai_tools: list[dict]) -> str:
    """Render a system-prompt tools preamble for DeepSeek-family models.

    DeepSeek-Coder-V2-Lite-Instruct has no native tool-call format in its
    chat template, so LM Studio can't translate the OpenAI ``tools`` array
    into anything the model sees. We bridge that by listing the tools in
    the system prompt and telling the model to emit calls as::

        <tool_call>{"name": "TOOL_NAME", "arguments": {...}}</tool_call>

    The existing ``_JSON_TOOL_PATTERNS[1]`` parser already matches this
    format, and the existing ``display_content`` scrub already strips it
    from the chat UI, so no additional parsing or scrubbing is required.
    """
    if not openai_tools:
        return ""

    lines: list[str] = [
        "You have access to the following tools. When you need to act, "
        "call a tool — do not describe what you would do, do it.",
        "",
        "Available tools:",
    ]
    for t in openai_tools:
        fn = t.get("function", {})
        name = fn.get("name", "")
        description = fn.get("description", "")
        params = fn.get("parameters", {})
        lines.append(f"- {name}: {description}")
        lines.append(f"  parameters: {json.dumps(params)}")
    lines.extend([
        "",
        "Wire format. Emit each tool call exactly as:",
        '  <tool_call>{"name": "TOOL_NAME", "arguments": {"arg": "value"}}</tool_call>',
        "",
        "Use only the tools listed above. After a tool result is returned, "
        "continue with another tool call or a final answer.",
    ])
    return "\n".join(lines)


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


def _parse_gemma_tool_calls(text: str, known_tools: set[str]) -> list[tuple[str, str]]:
    """Extract tool calls from Gemma 4's native format.

    Format: <|tool_call>call:func_name{key:<|"|>value<|"|>}<tool_call|>
    """
    results = []
    for m in _GEMMA_TOOL_CALL_PATTERN.finditer(text):
        name = m.group("name").strip()
        if name not in known_tools:
            continue
        body = m.group("body")
        args = {}
        for pm in _GEMMA_PARAM_PATTERN.finditer(body):
            key = pm.group("key").strip()
            value = pm.group("value")
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
    - Gemma 4: <|tool_call>call:name{key:<|"|>value<|"|>}<tool_call|>

    Returns list of (tool_name, arguments_json) tuples.
    Only returns matches where the tool name is in known_tools.
    """
    # Try Gemma format first (most specific delimiters)
    results = _parse_gemma_tool_calls(text, known_tools)
    if results:
        return results

    # Try Qwen3 XML format
    results = _parse_qwen3_xml_calls(text, known_tools)
    if results:
        return results

    # Fall back to JSON patterns. Stop at the first pattern that yields any
    # matches — each is a wrapper around the same inner JSON, so trying
    # multiple patterns would duplicate-match a single call.
    for pattern in _JSON_TOOL_PATTERNS:
        pattern_results: list[tuple[str, str]] = []
        for m in pattern.finditer(text):
            name = m.group("name")
            args = m.group("args")
            if name in known_tools:
                try:
                    json.loads(args)  # validate JSON
                    pattern_results.append((name, args))
                except json.JSONDecodeError:
                    continue
        if pattern_results:
            return pattern_results
    return results


def _obj_get(obj, key: str, default=None):
    """Read an SDK object, pydantic model, or dict without depending on shape."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    value = getattr(obj, key, default)
    if value is not default:
        return value
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump().get(key, default)
        except Exception:
            return default
    return default


def _status_event(
    phase: str,
    *,
    turn: int,
    detail: str = "",
    started_at: float,
    first_token_at: float | None = None,
    generated_chars: int | None = None,
) -> AgentEvent:
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    first_token_ms = (
        int((first_token_at - started_at) * 1000)
        if first_token_at is not None else None
    )
    rate: float | None = None
    if generated_chars is not None and first_token_at is not None:
        gen_elapsed = max(0.001, time.perf_counter() - first_token_at)
        rate = generated_chars / gen_elapsed
    return AgentEvent(
        kind="llm_status",
        phase=phase,
        content=detail,
        turn=turn,
        elapsed_ms=elapsed_ms,
        first_token_ms=first_token_ms,
        generated_chars=generated_chars,
        rate_chars_per_sec=rate,
    )


def _response_from_stream_parts(
    *,
    content: str,
    reasoning: str,
    tool_calls_by_index: dict[int, dict],
    finish_reason: str | None,
):
    tool_calls = []
    for index in sorted(tool_calls_by_index):
        tc = tool_calls_by_index[index]
        fn = tc.get("function", {})
        tool_calls.append(SimpleNamespace(
            id=tc.get("id") or _uuid.uuid4().hex,
            function=SimpleNamespace(
                name=fn.get("name") or "",
                arguments=fn.get("arguments") or "",
            ),
        ))

    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or None,
        role="assistant",
        model_dump=lambda: {"reasoning": reasoning or None},
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason=finish_reason or "stop")]
    )


def _safe_stream_text_prefix(delta: str) -> str:
    """Return the part of a streamed delta safe to show before final scrubbing.

    Local models sometimes emit tool calls or thinking blocks in plain text.
    Once one of those markers appears, the final post-processing path will
    handle the rest after it can scrub the complete message.
    """
    cut = len(delta)
    for marker in _STREAM_SUPPRESS_MARKERS:
        idx = delta.find(marker)
        if idx >= 0:
            cut = min(cut, idx)
    return delta[:cut]


class OpenAICompatBackend(Backend):
    """Backend that uses an OpenAI-compatible API (Ollama, vLLM, etc.)."""

    def __init__(
        self,
        tools: list,
        model: str,
        api_base: str = "http://localhost:11434/v1",
        api_key: str = "not-needed",
        model_family: str | None = None,
    ):
        self._model = model
        if model_family is None:
            self._family = "deepseek" if _is_deepseek_family(model) else "generic"
        else:
            self._family = model_family
        self._openai_tools, self._handlers = mcp_tools_to_openai(tools)
        self._tool_names = set(self._handlers.keys())
        self._client = AsyncOpenAI(
            base_url=api_base,
            api_key=api_key,
        )

    def _filtered_tools(
        self, allowed_tools: list[str] | None,
    ) -> tuple[list[dict], set[str]]:
        """Return (openai_tool_defs, known_names) filtered by allowed_tools.

        allowed_tools entries follow the MCP convention used by the Claude
        backend: ``mcp__re__<tool_name>`` or glob ``mcp__re__*``.  We strip
        the ``mcp__re__`` prefix and match against the bare tool name.
        """
        if not allowed_tools:
            return self._openai_tools, self._tool_names

        # Expand allowed set — strip mcp__re__ prefix, honour "*" wildcard
        allow_all = any(a == "mcp__re__*" or a == "*" for a in allowed_tools)
        if allow_all:
            return self._openai_tools, self._tool_names

        bare_names = set()
        for a in allowed_tools:
            if a.startswith("mcp__re__"):
                bare_names.add(a[len("mcp__re__"):])
            else:
                bare_names.add(a)

        filtered = [t for t in self._openai_tools
                     if t["function"]["name"] in bare_names]
        names = {t["function"]["name"] for t in filtered}
        return filtered, names

    async def run(
        self,
        prompt: str,
        system_prompt: str,
        *,
        max_turns: int = 50,
        max_budget_usd: float = 5.0,
        allowed_tools: list[str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        tools_for_model, tool_names = self._filtered_tools(allowed_tools)

        if self._family == "deepseek" and tools_for_model:
            system_prompt = (
                system_prompt
                + "\n\n"
                + _build_deepseek_tools_preamble(tools_for_model)
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        turn = 0
        has_used_tools = False

        while turn < max_turns:
            turn += 1
            yield AgentEvent(kind="turn", turns=turn, turn=turn)

            llm_started_at = time.perf_counter()
            yield _status_event(
                "prompt_processing",
                turn=turn,
                detail="request submitted to model backend",
                started_at=llm_started_at,
            )

            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools_for_model if tools_for_model else None,
                    extra_body={"think": True},
                    stream=True,
                )
            except Exception as e:
                err = str(e)
                if "n_keep" in err and "n_ctx" in err:
                    err = (
                        f"{err}\n\n"
                        "The model's context window is too small for the "
                        "agent prompt + tools. In LM Studio, select the model "
                        "and increase 'Context Length' to at least 16384 "
                        "(32768 recommended), then reload the model."
                    )
                yield AgentEvent(kind="error", content=err, is_error=True)
                yield AgentEvent(kind="result", content=f"Error: {err}", subtype="error")
                return

            if hasattr(response, "__aiter__"):
                content_parts: list[str] = []
                reasoning_parts: list[str] = []
                tool_calls_by_index: dict[int, dict] = {}
                finish_reason: str | None = None
                first_token_at: float | None = None
                last_status_at = llm_started_at
                generated_chars = 0
                stream_text_suppressed = False
                streamed_visible_parts: list[str] = []

                async for chunk in response:
                    choices = _obj_get(chunk, "choices", []) or []
                    if not choices:
                        continue
                    choice0 = choices[0]
                    finish_reason = _obj_get(choice0, "finish_reason", finish_reason)
                    delta = _obj_get(choice0, "delta")

                    content_delta = _obj_get(delta, "content", "") or ""
                    reasoning_delta = (
                        _obj_get(delta, "reasoning", None)
                        or _obj_get(delta, "reasoning_content", None)
                        or ""
                    )
                    tool_deltas = _obj_get(delta, "tool_calls", None) or []

                    if content_delta or reasoning_delta or tool_deltas:
                        now = time.perf_counter()
                        if first_token_at is None:
                            first_token_at = now
                            yield _status_event(
                                "generating",
                                turn=turn,
                                detail="first model output received",
                                started_at=llm_started_at,
                                first_token_at=first_token_at,
                                generated_chars=generated_chars,
                            )

                    if content_delta:
                        content_parts.append(content_delta)
                        generated_chars += len(content_delta)
                        if not stream_text_suppressed:
                            safe_prefix = _safe_stream_text_prefix(content_delta)
                            if safe_prefix:
                                streamed_visible_parts.append(safe_prefix)
                                yield AgentEvent(kind="text", content=safe_prefix, turn=turn)
                            if len(safe_prefix) < len(content_delta):
                                stream_text_suppressed = True
                    if reasoning_delta:
                        reasoning_parts.append(reasoning_delta)
                        generated_chars += len(reasoning_delta)

                    for tc_delta in tool_deltas:
                        index = int(_obj_get(tc_delta, "index", 0) or 0)
                        acc = tool_calls_by_index.setdefault(
                            index, {"id": "", "function": {"name": "", "arguments": ""}},
                        )
                        tc_id = _obj_get(tc_delta, "id", None)
                        if tc_id:
                            acc["id"] = tc_id
                        fn_delta = _obj_get(tc_delta, "function", None)
                        if fn_delta is not None:
                            name_delta = _obj_get(fn_delta, "name", "") or ""
                            args_delta = _obj_get(fn_delta, "arguments", "") or ""
                            acc["function"]["name"] += name_delta
                            acc["function"]["arguments"] += args_delta

                    now = time.perf_counter()
                    if first_token_at is not None and now - last_status_at >= _STATUS_EMIT_INTERVAL_SEC:
                        yield _status_event(
                            "generating",
                            turn=turn,
                            detail="model output streaming",
                            started_at=llm_started_at,
                            first_token_at=first_token_at,
                            generated_chars=generated_chars,
                        )
                        last_status_at = now

                yield _status_event(
                    "complete",
                    turn=turn,
                    detail="model response complete",
                    started_at=llm_started_at,
                    first_token_at=first_token_at,
                    generated_chars=generated_chars,
                )
                response = _response_from_stream_parts(
                    content="".join(content_parts),
                    reasoning="".join(reasoning_parts),
                    tool_calls_by_index=tool_calls_by_index,
                    finish_reason=finish_reason,
                )
                streamed_visible_text = "".join(streamed_visible_parts)
            else:
                yield _status_event(
                    "complete",
                    turn=turn,
                    detail="model response complete",
                    started_at=llm_started_at,
                )
                streamed_visible_text = ""

            choice = response.choices[0]
            assistant_msg = choice.message

            # Accumulate the assistant message for conversation history
            messages.append(_message_to_dict(assistant_msg))

            # Extract thinking from the reasoning field (Ollama/Qwen3.5)
            msg_data = assistant_msg.model_dump() if hasattr(assistant_msg, 'model_dump') else {}
            reasoning = msg_data.get("reasoning") or ""
            if reasoning.strip():
                yield AgentEvent(kind="thinking", content=reasoning.strip(), turn=turn)

            # Process content — extract any remaining thinking blocks in text
            raw_content = assistant_msg.content or ""

            # Extract <think>...</think> blocks as thinking events
            thinking_match = re.search(r'<think>(.*?)</think>', raw_content, re.DOTALL)
            if thinking_match:
                think_text = thinking_match.group(1).strip()
                if think_text and not reasoning:
                    yield AgentEvent(kind="thinking", content=think_text, turn=turn)
                # Remove thinking block from content for further processing
                display_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            else:
                display_content = raw_content

            # Extract Gemma <|channel>thought...<channel|> blocks
            for gemma_think in _GEMMA_THINKING_PATTERN.finditer(display_content):
                think_text = gemma_think.group("content").strip()
                if think_text and not reasoning:
                    yield AgentEvent(kind="thinking", content=think_text, turn=turn)
            display_content = _GEMMA_THINKING_PATTERN.sub('', display_content).strip()

            # Also handle unclosed <think> tags (known Qwen3.5 bug)
            if display_content.startswith('<think>'):
                think_text = display_content[7:].strip()
                if think_text and not reasoning:
                    yield AgentEvent(kind="thinking", content=think_text, turn=turn)
                display_content = ""

            # Emit visible text content (stripped of thinking/tool XML)
            visible_text = re.sub(r'<tool_call>.*?</tool_call>', '', display_content, flags=re.DOTALL).strip()
            # Also strip Gemma tool call markers
            visible_text = re.sub(r'<\|tool_call>.*?<tool_call\|>', '', visible_text, flags=re.DOTALL).strip()
            # Also strip ```json ...``` fences that the JSON extractor will
            # execute as tool calls. DeepSeek-Coder-V2 emits these in two
            # shapes — a bare object or a JSON array of objects — and may
            # batch multiple calls per fence. Match any fence whose body
            # carries a "name": "..." / "arguments": ... shape and remove
            # the whole fence (single regex covers both shapes). A "no
            # backticks inside" body keeps the match anchored to one fence.
            visible_text = _TOOL_CALL_FENCE_SCRUB.sub('', visible_text).strip()
            if visible_text:
                if streamed_visible_text and visible_text.strip() == streamed_visible_text.strip():
                    visible_text = ""
                elif streamed_visible_text and visible_text.startswith(streamed_visible_text):
                    visible_text = visible_text[len(streamed_visible_text):].lstrip()
                if not visible_text:
                    pass
                else:
                    yield AgentEvent(kind="text", content=visible_text, turn=turn)

            # Check for tool calls
            if not assistant_msg.tool_calls:
                # Some models output tool calls as plain text (JSON or XML)
                # instead of using the structured tool_calls field.
                text_calls = []
                if raw_content:
                    text_calls = _extract_text_tool_calls(
                        raw_content, tool_names,
                    )

                if text_calls:
                    has_used_tools = True
                    # Execute the tool calls the model embedded in its text.
                    # Text-extracted calls have no native id, so synthesize one
                    # per call. The renderer keys ToolCallChip on this id, so
                    # two text-extracted calls in the same turn must not collide.
                    result_parts = []
                    for name, args in text_calls:
                        tool_use_id = _uuid.uuid4().hex
                        yield AgentEvent(
                            kind="tool_call",
                            tool_name=name,
                            tool_input=args,
                            tool_use_id=tool_use_id,
                            turn=turn,
                        )

                        result_text, is_error = await execute_tool(
                            self._handlers, name, args,
                            allowed_set=tool_names if allowed_tools else None,
                        )

                        yield AgentEvent(
                            kind="tool_result",
                            content=result_text,
                            is_error=is_error,
                            tool_use_id=tool_use_id,
                            turn=turn,
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
                # `tc.id` is the OpenAI tool_call_id (cf. _message_to_dict
                # below). The renderer pairs tool_result with tool_call by
                # this id, and uses it as the React key — so it must be
                # populated and unique within the turn.
                tool_use_id = tc.id or _uuid.uuid4().hex
                yield AgentEvent(
                    kind="tool_call",
                    tool_name=fn.name,
                    tool_input=fn.arguments,
                    tool_use_id=tool_use_id,
                    turn=turn,
                )

                result_text, is_error = await execute_tool(
                    self._handlers, fn.name, fn.arguments,
                    allowed_set=tool_names if allowed_tools else None,
                )

                yield AgentEvent(
                    kind="tool_result",
                    content=result_text,
                    is_error=is_error,
                    tool_use_id=tool_use_id,
                    turn=turn,
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
