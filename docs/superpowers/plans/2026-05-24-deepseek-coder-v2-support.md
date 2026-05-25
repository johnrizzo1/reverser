# DeepSeek-Coder-V2-Lite Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DeepSeek-Coder-V2-Lite-Instruct (running under LM Studio) reliably emit tool calls that the OpenAI-compatible backend can parse and execute.

**Architecture:** Add per-family awareness to `OpenAICompatBackend`. When the model name contains `deepseek` (or `--model-family deepseek` is passed), append a tools preamble to the system prompt teaching the model the `<tool_call>{"name": "...", "arguments": {...}}</tool_call>` wire format — which the existing JSON parser and existing visible-text scrub already handle. No new parsers, no new scrubbing.

**Tech Stack:** Python 3.12+, `openai` async client, pytest, pytest-asyncio.

---

## File Structure

**Files modified:**
- `src/reverser/backends/openai_compat.py` — add `_is_deepseek_family`, `_build_deepseek_tools_preamble`, accept `model_family` in `__init__`, inject preamble in `run()`.
- `src/reverser/backends/__init__.py` — add `model_family` kwarg to `create_backend`, forward to `OpenAICompatBackend`.
- `src/reverser/cli.py` — add `--model-family` flag to the shared `add_backend_args` helper, plumb to `run_agent`.
- `src/reverser/agent.py` — accept `model_family` kwarg in `run_agent`, forward to `create_backend`.
- `README.md` — one paragraph + flag mention.

**Files created:**
- `tests/test_openai_compat_deepseek.py` — unit tests for `_is_deepseek_family`, `_build_deepseek_tools_preamble`, end-to-end preamble→parser interaction, smoke test of `OpenAICompatBackend.run()` with stub.

**Files NOT touched:**
- `src/reverser/agent_session.py` — `create_backend` is called there without `model_family`, so it gets the default (`None` → auto-detect). Auto-detection by model name covers the common case. Adding session-config persistence is YAGNI for this change.
- `src/reverser/gui_service/*` — same reason. GUI sessions call `create_backend` without `model_family`; auto-detection still works.
- `src/reverser/backends/claude.py` — Claude backend is unaffected.

---

### Task 1: Add `_is_deepseek_family` helper

**Files:**
- Modify: `src/reverser/backends/openai_compat.py`
- Test: `tests/test_openai_compat_deepseek.py`

- [ ] **Step 1: Create the test file with the family-detection tests**

Create `tests/test_openai_compat_deepseek.py`:

```python
"""DeepSeek-family support in OpenAICompatBackend.

Coder-V2-Lite has no native tool-call template, so we teach it the
<tool_call>{...}</tool_call> JSON format via a system-prompt preamble.
The existing _JSON_TOOL_PATTERNS already parses that format, so no new
parser is needed.
"""
import pytest

from reverser.backends.openai_compat import _is_deepseek_family


@pytest.mark.parametrize("name", [
    "deepseek-coder-v2-lite-instruct",
    "DeepSeek-V2",
    "deepseek-r1:7b",
    "lmstudio-community/DeepSeek-Coder-V2-Lite-Instruct-GGUF",
])
def test_is_deepseek_family_true(name):
    assert _is_deepseek_family(name) is True


@pytest.mark.parametrize("name", [
    "qwen3.5-coder",
    "gemma-3-27b",
    "llama-3.3",
    "",
    None,
])
def test_is_deepseek_family_false(name):
    assert _is_deepseek_family(name) is False
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
pytest tests/test_openai_compat_deepseek.py -v
```

Expected: ImportError on `_is_deepseek_family`.

- [ ] **Step 3: Implement `_is_deepseek_family`**

Add to `src/reverser/backends/openai_compat.py`, after the existing regex patterns and before `_parse_qwen3_xml_calls` (around line 76):

```python
def _is_deepseek_family(model: str | None) -> bool:
    """True for any DeepSeek model name (covers Coder-V2, V2, V2.5, R1).

    Detection is by substring so that LM Studio's full GGUF paths
    (e.g. ``lmstudio-community/DeepSeek-Coder-V2-Lite-Instruct-GGUF``)
    match as well as bare tags.
    """
    return "deepseek" in (model or "").lower()
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
pytest tests/test_openai_compat_deepseek.py -v
```

Expected: 9 passing (4 + 5 parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add src/reverser/backends/openai_compat.py tests/test_openai_compat_deepseek.py
git commit -m "feat(backends): add DeepSeek family detection helper

Identifies deepseek-coder-v2-lite-instruct and other DeepSeek tags by
substring match. First step toward injecting a tools preamble for
DeepSeek models in LM Studio.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add `_build_deepseek_tools_preamble`

**Files:**
- Modify: `src/reverser/backends/openai_compat.py`
- Test: `tests/test_openai_compat_deepseek.py`

- [ ] **Step 1: Add the preamble tests**

Append to `tests/test_openai_compat_deepseek.py`:

```python
from reverser.backends.openai_compat import _build_deepseek_tools_preamble


def _fake_tool(name, description, params_schema):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params_schema,
        },
    }


def test_preamble_lists_each_tool_name_and_description():
    tools = [
        _fake_tool("nmap_scan", "Run an nmap scan",
                   {"type": "object", "properties": {"target": {"type": "string"}}}),
        _fake_tool("ghidra_decompile", "Decompile a function",
                   {"type": "object", "properties": {"addr": {"type": "string"}}}),
    ]
    preamble = _build_deepseek_tools_preamble(tools)
    assert "nmap_scan" in preamble
    assert "Run an nmap scan" in preamble
    assert "ghidra_decompile" in preamble
    assert "Decompile a function" in preamble


def test_preamble_contains_wire_format_markers():
    tools = [_fake_tool("bash", "Run a shell command",
                        {"type": "object", "properties": {"cmd": {"type": "string"}}})]
    preamble = _build_deepseek_tools_preamble(tools)
    assert "<tool_call>" in preamble
    assert "</tool_call>" in preamble


def test_preamble_includes_parameter_schema():
    tools = [_fake_tool("bash", "Run a shell command",
                        {"type": "object", "properties": {"cmd": {"type": "string"}}})]
    preamble = _build_deepseek_tools_preamble(tools)
    # JSON schema fragment must appear in the preamble.
    assert '"cmd"' in preamble
    assert '"type": "string"' in preamble or '"type":"string"' in preamble


def test_preamble_with_empty_tools_returns_empty_string():
    """Defensive: call site already guards on tools_for_model, but the
    function itself should be lenient and return an empty string for [].
    """
    assert _build_deepseek_tools_preamble([]) == ""
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_openai_compat_deepseek.py -v
```

Expected: ImportError on `_build_deepseek_tools_preamble`.

- [ ] **Step 3: Implement `_build_deepseek_tools_preamble`**

Add to `src/reverser/backends/openai_compat.py` immediately after `_is_deepseek_family`:

```python
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
```

- [ ] **Step 4: Run, confirm pass**

```bash
pytest tests/test_openai_compat_deepseek.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/backends/openai_compat.py tests/test_openai_compat_deepseek.py
git commit -m "feat(backends): build DeepSeek tools preamble

Lists each tool's name, description, and parameter schema, plus the
<tool_call>{...}</tool_call> wire format the existing JSON parser
already understands. Will be injected into the system prompt for
DeepSeek-family models in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Pin the preamble↔parser contract with an end-to-end test

**Why this task exists:** Tasks 1–2 cover the preamble in isolation. The whole change rests on the assumption that the format the preamble teaches (`<tool_call>{...}</tool_call>`) is exactly what `_JSON_TOOL_PATTERNS[1]` already parses. Pin that invariant with an explicit test so a future regex tweak can't silently break DeepSeek.

**Files:**
- Test: `tests/test_openai_compat_deepseek.py`

- [ ] **Step 1: Add the contract test**

Append to `tests/test_openai_compat_deepseek.py`:

```python
from reverser.backends.openai_compat import _extract_text_tool_calls


def test_preamble_format_is_parseable_by_existing_extractor():
    """The wire format in the preamble must match what the existing
    text-tool-call extractor parses. This pins that invariant.
    """
    tools = [_fake_tool("nmap_scan", "Run nmap",
                        {"type": "object", "properties": {"target": {"type": "string"}}})]
    preamble = _build_deepseek_tools_preamble(tools)
    # Confirm the literal example in the preamble parses to a known tool call.
    assert '<tool_call>{"name": "TOOL_NAME"' in preamble  # documented format
    # And confirm an actual call in that format extracts cleanly.
    assistant_msg = (
        "I'll scan now.\n"
        '<tool_call>{"name": "nmap_scan", "arguments": {"target": "10.0.0.1"}}</tool_call>\n'
        "Standing by for results."
    )
    calls = _extract_text_tool_calls(assistant_msg, {"nmap_scan"})
    assert len(calls) == 1
    name, args_json = calls[0]
    assert name == "nmap_scan"
    import json as _json
    assert _json.loads(args_json) == {"target": "10.0.0.1"}


def test_unknown_tool_is_rejected_by_extractor():
    """Defense in depth — even if the model invents a tool, we don't run it."""
    assistant_msg = (
        '<tool_call>{"name": "rm_rf_slash", "arguments": {}}</tool_call>'
    )
    calls = _extract_text_tool_calls(assistant_msg, {"nmap_scan"})
    assert calls == []
```

- [ ] **Step 2: Run, confirm pass**

These are pure-Python tests against code that already exists (extractor) plus code from Tasks 1–2 (preamble).

```bash
pytest tests/test_openai_compat_deepseek.py -v
```

Expected: all 15 tests pass. If any fail it means either the preamble format drifted from the literal `<tool_call>{"name": "TOOL_NAME"` form, or the extractor regex has been changed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_openai_compat_deepseek.py
git commit -m "test(backends): pin DeepSeek preamble<->extractor contract

End-to-end test that ensures the wire format taught in the preamble
matches what _JSON_TOOL_PATTERNS already parses. Guards against either
side drifting independently.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire `model_family` into `OpenAICompatBackend`

**Files:**
- Modify: `src/reverser/backends/openai_compat.py` (around lines 161–175, 205–234)
- Test: `tests/test_openai_compat_deepseek.py`

- [ ] **Step 1: Add the smoke test for preamble injection**

Append to `tests/test_openai_compat_deepseek.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _mk_response(content, tool_calls=None, finish_reason="stop"):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.role = "assistant"
    msg.model_dump = lambda: {"reasoning": None}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)]
    )


@pytest.mark.asyncio
async def test_deepseek_preamble_is_appended_to_system_prompt(monkeypatch):
    """When family is deepseek and tools are present, the preamble is
    appended to the system prompt sent on the chat-completions request.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="deepseek-coder-v2-lite-instruct", api_key="x",
    )
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command",
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    create_mock.assert_called_once()
    sent_messages = create_mock.call_args.kwargs["messages"]
    system_msg = sent_messages[0]
    assert system_msg["role"] == "system"
    assert system_msg["content"].startswith("be helpful")
    # Preamble must be present.
    assert "<tool_call>" in system_msg["content"]
    assert "bash" in system_msg["content"]


@pytest.mark.asyncio
async def test_generic_family_does_not_get_preamble(monkeypatch):
    """A non-DeepSeek model must not get the preamble (no regression for
    Qwen3/Gemma/etc., which have their own paths).
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(tools=[], model="qwen3-coder", api_key="x")
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "bash", "description": "x", "parameters": {}},
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    sent_messages = create_mock.call_args.kwargs["messages"]
    assert sent_messages[0]["content"] == "be helpful"
    assert "<tool_call>" not in sent_messages[0]["content"]


@pytest.mark.asyncio
async def test_model_family_override_forces_deepseek(monkeypatch):
    """Even when the model name doesn't say 'deepseek', model_family='deepseek'
    forces preamble injection.
    """
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="custom-finetune-tag", api_key="x",
        model_family="deepseek",
    )
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "bash", "description": "x", "parameters": {}},
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    assert "<tool_call>" in create_mock.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_model_family_override_forces_generic(monkeypatch):
    """And model_family='generic' suppresses the preamble even on deepseek-named models."""
    from reverser.backends.openai_compat import OpenAICompatBackend

    backend = OpenAICompatBackend(
        tools=[], model="deepseek-coder-v2-lite-instruct", api_key="x",
        model_family="generic",
    )
    backend._handlers = {"bash": AsyncMock(return_value=("ok", False))}
    backend._tool_names = {"bash"}
    backend._openai_tools = [{
        "type": "function",
        "function": {"name": "bash", "description": "x", "parameters": {}},
    }]

    create_mock = AsyncMock(return_value=_mk_response("done"))
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    async for _ in backend.run(prompt="hi", system_prompt="be helpful", max_turns=1):
        pass

    assert "<tool_call>" not in create_mock.call_args.kwargs["messages"][0]["content"]
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_openai_compat_deepseek.py -v
```

Expected: the four new tests fail because `OpenAICompatBackend.__init__` doesn't accept `model_family` yet, or the preamble isn't injected.

- [ ] **Step 3: Update `OpenAICompatBackend.__init__` to accept `model_family`**

In `src/reverser/backends/openai_compat.py`, modify the `__init__` method (currently at lines 161–174):

```python
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
        # Family is auto-detected from the model name when unset.
        # Pass model_family="deepseek" or "generic" to override.
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
```

- [ ] **Step 4: Inject the preamble in `run()`**

Modify the start of `OpenAICompatBackend.run()` (currently at lines 213–219). The existing code looks like:

```python
    ) -> AsyncIterator[AgentEvent]:
        tools_for_model, tool_names = self._filtered_tools(allowed_tools)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
```

Change to:

```python
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
```

- [ ] **Step 5: Run all backend tests**

```bash
pytest tests/test_openai_compat_deepseek.py tests/test_openai_backend_ids.py tests/test_backends_allowlist.py tests/test_backend_factory.py -v
```

Expected: all pass. The previously-existing tests must not regress.

- [ ] **Step 6: Commit**

```bash
git add src/reverser/backends/openai_compat.py tests/test_openai_compat_deepseek.py
git commit -m "feat(backends): inject DeepSeek tools preamble into system prompt

OpenAICompatBackend now accepts model_family (auto-detected from model
name when None). When family is deepseek and tools are present, the
preamble built in the previous task is appended to the system prompt
on every chat-completions request. Coder-V2-Lite users now see tools
in the prompt and can call them via <tool_call>{...}</tool_call>,
which the existing JSON extractor handles.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Plumb `model_family` through `create_backend`

**Files:**
- Modify: `src/reverser/backends/__init__.py`
- Test: `tests/test_backend_factory.py`

- [ ] **Step 1: Add the factory test**

Append to `tests/test_backend_factory.py`:

```python
def test_model_family_passes_through_to_openai_compat():
    """create_backend forwards model_family to OpenAICompatBackend."""
    with patch("reverser.backends.openai_compat.OpenAICompatBackend") as M:
        create_backend(
            "lmstudio",
            tools=[],
            model="deepseek-coder-v2-lite-instruct",
            model_family="deepseek",
        )
        assert M.call_args.kwargs["model_family"] == "deepseek"


def test_model_family_defaults_to_none():
    """When omitted, model_family is None (auto-detect happens inside the backend)."""
    with patch("reverser.backends.openai_compat.OpenAICompatBackend") as M:
        create_backend("ollama", tools=[], model="qwen3-coder")
        assert M.call_args.kwargs.get("model_family") is None


def test_claude_factory_ignores_model_family():
    """Claude path doesn't accept model_family — passing it shouldn't crash."""
    with patch("reverser.backends.claude.ClaudeBackend") as M:
        create_backend("claude", tools=[], model_family="deepseek")
        M.assert_called_once_with([])
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_backend_factory.py -v
```

Expected: TypeError (unexpected keyword argument `model_family`).

- [ ] **Step 3: Update `create_backend`**

Modify `src/reverser/backends/__init__.py`. The existing function signature is:

```python
def create_backend(
    name: str,
    tools: list,
    *,
    model: str | None = None,
    api_base: str | None = None,
) -> Backend:
```

Change to:

```python
def create_backend(
    name: str,
    tools: list,
    *,
    model: str | None = None,
    api_base: str | None = None,
    model_family: str | None = None,
) -> Backend:
    """Factory to create a backend by name.

    Args:
        name: 'claude', 'ollama', 'lmstudio', or any OpenAI-compatible provider.
        tools: List of SdkMcpTool instances (from the tools package).
        model: Model name/tag. Required for non-claude backends.
        api_base: API base URL override. Defaults per backend.
        model_family: 'deepseek' | 'generic' | None. None (default) means
            auto-detect from the model name. Ignored by the Claude backend.
    """
    if name == "claude":
        from .claude import ClaudeBackend
        return ClaudeBackend(tools)

    if not model:
        raise ValueError(f"--model is required for backend '{name}'")

    if api_base is None:
        api_base = DEFAULT_API_BASES.get(name, _GENERIC_DEFAULT_API_BASE)

    from .openai_compat import OpenAICompatBackend
    return OpenAICompatBackend(
        tools=tools,
        model=model,
        api_base=api_base,
        model_family=model_family,
    )
```

- [ ] **Step 4: Run all factory tests**

```bash
pytest tests/test_backend_factory.py -v
```

Expected: all pass, including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/reverser/backends/__init__.py tests/test_backend_factory.py
git commit -m "feat(backends): plumb model_family through create_backend

Forwards the new model_family kwarg to OpenAICompatBackend; Claude
backend ignores it. Default None preserves all existing call sites
(agent.py, agent_session.py, gui_service) without modification —
auto-detection kicks in based on the model name.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Add `--model-family` CLI flag

**Files:**
- Modify: `src/reverser/cli.py` (around lines 46–54, 224–233)
- Modify: `src/reverser/agent.py` (around lines 51–53, 85–90)
- Test: `tests/test_cli.py` (existing — add cases)

- [ ] **Step 1: Add CLI help-text smoke tests**

`tests/test_cli.py` uses subprocess-based help-text checks. Match that pattern. Append:

```python
def test_triage_help_mentions_model_family():
    """--model-family appears in the analyze-command help output."""
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "triage", "--help"],
        capture_output=True, text=True,
    )
    assert "--model-family" in result.stdout, result.stdout
    assert "deepseek" in result.stdout, result.stdout


def test_interactive_help_mentions_model_family():
    """And in the interactive-command help too (same shared helper)."""
    result = subprocess.run(
        [PYTHON, "-m", "reverser", "interactive", "--help"],
        capture_output=True, text=True,
    )
    assert "--model-family" in result.stdout, result.stdout
```

- [ ] **Step 2: Run, confirm failure**

```bash
pytest tests/test_cli.py -v -k model_family
```

Expected: both new tests fail (string not found in `--help` output, because `--model-family` doesn't exist yet).

- [ ] **Step 3: Add `--model-family` to the shared `add_backend_args` helper**

In `src/reverser/cli.py`, modify `add_backend_args` (currently lines 47–54):

```python
    # Shared backend arguments
    def add_backend_args(sub):
        sub.add_argument("--backend", "-b", default="claude",
                         help="LLM backend: claude, ollama, lmstudio, or any "
                              "OpenAI-compatible server (default: claude)")
        sub.add_argument("--model", "-m", default=None,
                         help="Model name/tag for non-claude backends (e.g. qwen3.5:35b-a3b-coding-nvfp4)")
        sub.add_argument("--api-base", default=None,
                         help="API base URL override (default: http://localhost:11434/v1 for ollama)")
        sub.add_argument("--model-family", default=None, choices=["deepseek", "generic"],
                         help="Override model-family detection. By default the family "
                              "is inferred from the model name. Use 'deepseek' for "
                              "DeepSeek-derived models that don't have 'deepseek' in "
                              "the name.")
```

- [ ] **Step 4: Forward `model_family` in the `run_agent` call site**

In `src/reverser/cli.py`, find the `asyncio.run(run_agent(...))` block (currently around lines 224–233):

```python
    asyncio.run(run_agent(
        binary,
        mode=args.command,
        budget=args.budget,
        verbosity=args.verbose,
        log_path=log_path,
        backend_name=args.backend,
        model=args.model,
        api_base=args.api_base,
    ))
```

Change to:

```python
    asyncio.run(run_agent(
        binary,
        mode=args.command,
        budget=args.budget,
        verbosity=args.verbose,
        log_path=log_path,
        backend_name=args.backend,
        model=args.model,
        api_base=args.api_base,
        model_family=args.model_family,
    ))
```

- [ ] **Step 5: Accept and forward `model_family` in `run_agent`**

In `src/reverser/agent.py`, modify the `run_agent` signature (around lines 50–55) and the `create_backend` call (around lines 85–90).

The existing signature should look like:

```python
async def run_agent(
    binary: str | None,
    *,
    mode: str = "analyze",
    budget: float = 2.0,
    verbosity: int = 0,
    log_path: Path | None = None,
    backend_name: str = "claude",
    model: str | None = None,
    api_base: str | None = None,
):
```

Change to:

```python
async def run_agent(
    binary: str | None,
    *,
    mode: str = "analyze",
    budget: float = 2.0,
    verbosity: int = 0,
    log_path: Path | None = None,
    backend_name: str = "claude",
    model: str | None = None,
    api_base: str | None = None,
    model_family: str | None = None,
):
```

And update the `create_backend` call inside the function (around lines 85–90):

```python
    backend = create_backend(
        backend_name,
        tools,
        model=model,
        api_base=api_base,
    )
```

Change to:

```python
    backend = create_backend(
        backend_name,
        tools,
        model=model,
        api_base=api_base,
        model_family=model_family,
    )
```

Note: if the docstring inside `run_agent` lists each parameter, add a one-line entry for `model_family` matching the existing style.

- [ ] **Step 6: Run CLI tests and the full backend suite**

```bash
pytest tests/test_cli.py tests/test_openai_compat_deepseek.py tests/test_backend_factory.py tests/test_openai_backend_ids.py -v
```

Expected: all pass, including the two new `model_family` help-text tests.

- [ ] **Step 7: Commit**

```bash
git add src/reverser/cli.py src/reverser/agent.py tests/test_cli.py
git commit -m "feat(cli): add --model-family flag for backend family override

Plumbs through cli.py -> run_agent() -> create_backend(). Choices are
'deepseek' or 'generic'; default is auto-detect from the model name.
Lets users force the DeepSeek preamble on a fine-tune whose name
doesn't contain 'deepseek', or suppress it on a model where it isn't
helpful.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md` (around lines 155–177, the "Backend selection" section)

- [ ] **Step 1: Update the Backend selection section**

In `README.md`, the "Backend selection" section (line 155) currently has example invocations followed by a flag list. Add a DeepSeek example and the new flag.

Find the example block (lines 157–169):

```sh
# Claude (default)
reverser i <target>

# LM Studio (auto-detects http://localhost:1234/v1)
reverser i <target> -b lmstudio -m qwen3.6-35b-a3b-ud-mlx

# Ollama (auto-detects http://localhost:11434/v1)
reverser i <target> -b ollama -m qwen3.5:35b-a3b-coding-nvfp4

# Any OpenAI-compatible endpoint
reverser i <target> -b local -m model-name --api-base http://gpu-server:8000/v1
```

Add a DeepSeek line:

```sh
# Claude (default)
reverser i <target>

# LM Studio (auto-detects http://localhost:1234/v1)
reverser i <target> -b lmstudio -m qwen3.6-35b-a3b-ud-mlx

# DeepSeek-Coder-V2-Lite in LM Studio (auto-applies tool-call preamble)
reverser i <target> -b lmstudio -m deepseek-coder-v2-lite-instruct

# Ollama (auto-detects http://localhost:11434/v1)
reverser i <target> -b ollama -m qwen3.5:35b-a3b-coding-nvfp4

# Any OpenAI-compatible endpoint
reverser i <target> -b local -m model-name --api-base http://gpu-server:8000/v1
```

And update the flag list (lines 173–177):

```
-b, --backend       Backend: claude (default), ollama, lmstudio, or any name for OpenAI-compatible
-m, --model         Model name/tag (required for non-claude backends)
--api-base          API base URL override
--model-family      Force model family: deepseek or generic (default: auto-detect from --model)
```

Add a short note paragraph immediately after the flag list:

```markdown
**DeepSeek-Coder-V2-Lite:** This model's chat template has no native
tool-call format, so the agent automatically appends a tools preamble to
the system prompt that teaches it the `<tool_call>{...}</tool_call>`
wire format. Detection is by `deepseek` substring in `--model`. Override
with `--model-family deepseek` (force on) or `--model-family generic`
(force off).
```

- [ ] **Step 2: Spot-check the rendered README**

```bash
grep -A 5 "DeepSeek-Coder-V2-Lite" README.md
```

Expected: the new code block and paragraph render in order.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README mentions DeepSeek-Coder-V2-Lite and --model-family

Documents the new auto-detected tool-call preamble for DeepSeek-family
models running under LM Studio, and the --model-family escape hatch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: no failures attributable to this change. Any pre-existing failures should be the same as before this branch started.

- [ ] **Step 2: Manual smoke test with LM Studio**

This step requires a running LM Studio instance with `deepseek-coder-v2-lite-instruct` loaded and the OpenAI server enabled (context length ≥ 16384).

```bash
# Confirm the model is reachable
curl http://localhost:1234/v1/models | head -20

# Run a quick interactive session
python -m reverser i some-test-target -b lmstudio -m deepseek-coder-v2-lite-instruct --max-turns 3
```

Confirm in the TUI:
- A tool fires (chip appears, result rendered).
- No `<tool_call>` or `</tool_call>` literal text leaks into the chat output.
- Model does not just narrate ("I would call…") — it actually calls.

If the model still narrates instead of calling: check `python -m reverser i ... -vv` for the system prompt; the preamble should be visible at the end. If absent, the family wasn't detected — verify the model tag contains `deepseek`.

- [ ] **Step 3: Final commit and summary**

If any minor fixes came out of manual verification, commit them. Otherwise nothing more to do.

```bash
git log --oneline main..HEAD
```

Should show the 7 commits from Tasks 1–7.

---

## Self-Review Notes

**Spec coverage:**
- ✅ Detection (Task 1)
- ✅ Tools preamble (Task 2)
- ✅ Preamble-injection wiring in `run()` (Task 4)
- ✅ CLI flag `--model-family` with `deepseek` / `generic` / auto (Task 6)
- ✅ `create_backend` plumbing (Task 5)
- ✅ Unit tests for all three (Tasks 1, 2, 3, 4)
- ✅ Smoke test through `OpenAICompatBackend.run()` (Task 4)
- ✅ Manual verification checklist (Task 8)
- ✅ README update (Task 7)
- ✅ No regression for generic-family models (Task 4 includes explicit generic-family negative test)

**Placeholder scan:** None. Every code change includes the exact code; every test includes the exact assertions.

**Type consistency:** `model_family` is `str | None` everywhere (`_is_deepseek_family` accepts `str | None`, `OpenAICompatBackend.__init__` accepts `model_family: str | None = None`, `create_backend` and `run_agent` and CLI all use `str | None`). The internal `self._family` is always `"deepseek"` or `"generic"` (never `None`) after `__init__`.

**Out-of-scope deferrals:**
- `agent_session.py` and `gui_service/` don't pass `model_family` to `create_backend`; they fall back to the default `None`, which auto-detects from the model name. Adequate for the user's stated use case. If session-config persistence of `model_family` is needed later, that's a separate change.
- DeepSeek-V3 native marker support (the `<｜tool▁call▁begin｜>` family) is explicitly out of scope per the revised spec.
