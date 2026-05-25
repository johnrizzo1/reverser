# DeepSeek-Coder-V2 (Lite) Support — Design

**Status:** Draft (revised 2026-05-24 after verifying chat template)
**Date:** 2026-05-24
**Owner:** John Rizzo
**Target model:** `deepseek-coder-v2-lite-instruct` (16B MoE, 2.4B active), via LM Studio GGUF.

## Problem

Today, running DeepSeek-Coder-V2-Lite-Instruct through `OpenAICompatBackend`
against an LM Studio endpoint fails to drive tool calls. The model narrates
what it would do ("I'll call `nmap_scan` now…") but no `tool_call` event ever
fires.

Root cause: DeepSeek-Coder-V2-Lite-Instruct's official chat template (from
`tokenizer_config.json` in
[`deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct`](https://huggingface.co/deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct))
has **no tool-calling format at all**. It's a plain `User: …Assistant: …`
prose template with `<｜begin▁of▁sentence｜>` / `<｜end▁of▁sentence｜>` BOS/EOS
and nothing tool-related. The model-card README documents no tool-use
prompt format either. Unlike DeepSeek-V3 (which has the full
`<｜tool▁call▁begin｜>` / `<｜tool▁sep｜>` / `<｜tool▁outputs▁begin｜>` marker
suite baked into its chat template), Coder-V2-Lite was never trained on any
specific tool-call syntax.

LM Studio therefore has nothing to render the OpenAI `tools` array into when
it tokenizes the request. The model receives a prompt with no mention of
tools, so it can't call them. It defaults to prose.

The user also reported that "thinking" looks broken. DeepSeek-Coder-V2-Lite
is not a reasoning model — it has no `reasoning_content` field and no
`<think>` blocks. Apparent "thinking" is the model improvising a plan in
plain text before failing to emit a tool call. Once tool calling works,
that prose either disappears or shows up as a normal assistant text turn —
which is correct.

## Goals

1. DeepSeek-Coder-V2-Lite-Instruct in LM Studio reliably emits tool calls
   that the backend can parse and execute.
2. Tool results are fed back in a form the model can act on.
3. No regression for any existing model family (Claude, Qwen3, Gemma, generic
   OpenAI-compatible).

## Non-goals

- DeepSeek-R1 / V3-Thinking support. Different model class, different
  `reasoning_content` field, separate task.
- DeepSeek-V3 / V2.5-Coder native marker parsing. V3 has a real
  `<｜tool▁call▁begin｜>` / `<｜tool▁sep｜>` chat template, and supporting it
  fully is a separate piece of work. This change targets Coder-V2-Lite
  specifically, which has no native format. We use a format the model can
  comply with via prompt instructions; if someone runs V3 locally, that's a
  follow-up.
- Refactoring the existing Qwen3 / Gemma parser logic into a `ModelFamilyAdapter`
  interface. That's a worthwhile cleanup but explicitly deferred — this change
  follows the existing per-family-detection pattern.
- Tweaking sampling parameters. The current backend uses model defaults
  across the board and we keep that consistent here.
- Native-API DeepSeek support via `api.deepseek.com`. The official API
  delivers structured `tool_calls` correctly via the existing
  OpenAI-compatible path; this design targets the LM Studio gap.

## Architecture

All changes live in `src/reverser/backends/openai_compat.py` alongside the
existing per-family logic (Qwen3, Gemma). One CLI argument is added in
`src/reverser/cli.py`. No new files in the backend; tests go in a new file.

Two axes:

1. **Detection** — recognize the DeepSeek family at backend construction.
2. **Prompt augmentation** — append a tools-preamble to the system prompt
   for DeepSeek models so the model sees the tool list and the expected
   wire format.

**Note on output parsing:** the existing
`_JSON_TOOL_PATTERNS[1]` already matches
`<tool_call>{"name": "...", "arguments": {...}}</tool_call>`, and the
existing visible-text scrub in `OpenAICompatBackend.run()` already strips
`<tool_call>…</tool_call>` from displayed content. We instruct the model to
use exactly that format in the preamble, so no new parser and no new
scrubbing are required.

### Detection

New module-level helper:

```python
def _is_deepseek_family(model: str) -> bool:
    return "deepseek" in (model or "").lower()
```

Covers `deepseek-coder-v2-lite-instruct`, `deepseek-coder-v2`, `deepseek-v2`,
`deepseek-v2.5`, `deepseek-r1`, and any future variant that keeps the
`deepseek` substring. We treat them all the same for the purpose of *this*
change: get the model to see tools by injecting a preamble. The narrower V3
question (using its native markers properly) is explicitly out of scope.

`OpenAICompatBackend.__init__` computes `self._family` once, stored as
`"deepseek"` or `"generic"`. All new behavior keys off this string.

**Override.** A new CLI flag `--model-family` accepts `deepseek` | `generic`
| `auto` (default `auto`). It's plumbed through `create_backend(...)` into
`OpenAICompatBackend(family=...)`. Escape hatch for fine-tunes whose names
don't carry `deepseek` (or vice versa). Claude backend ignores it.

### Prompt augmentation: tools preamble

New module-level function:

```python
def _build_deepseek_tools_preamble(openai_tools: list[dict]) -> str: ...
```

Returns a text block containing:

1. Intent statement: *"You have access to the following tools. When you need
   to act, call a tool — do not describe what you would do, do it."*
2. A list of tools. For each, the bare `name`, the `description`, and
   `json.dumps(tool["function"]["parameters"])` for the parameter schema.
3. The exact wire format the model should produce, shown literally so the
   model copies it:
   ```
   <tool_call>{"name": "TOOL_NAME", "arguments": {"arg": "value"}}</tool_call>
   ```
4. A trailing rule: *"Output one or more `<tool_call>…</tool_call>` blocks
   when you need to act. Use only the tools listed above. After a tool
   result, continue with another tool call or a final answer."*

This format was chosen because the existing
`_JSON_TOOL_PATTERNS[1]` regex in `openai_compat.py` already matches it,
and the existing `display_content` scrub already strips it from chat
output. So the preamble alone is enough to close the loop — no new parser
work, no new scrubbing.

**Injection site.** In `OpenAICompatBackend.run()`, after `system_prompt`
arrives as a parameter and after `tools_for_model` has been computed by
`_filtered_tools(...)`:

```python
if self._family == "deepseek" and tools_for_model:
    system_prompt = (
        system_prompt
        + "\n\n"
        + _build_deepseek_tools_preamble(tools_for_model)
    )
```

Appending (rather than prepending or adding a second system message) keeps
the existing profile-driven system prompt structure intact and avoids the
risk of LM Studio's chat template honoring only the first system message.

### Tool-result framing

The existing text-extracted-tool-call path feeds results back as a `user`
message containing `"[Tool result: NAME — OK|ERROR]\n<result>"` plus a
"continue your analysis" nudge. That keeps working for DeepSeek as-is.

The structured `tool_calls` path (when the engine *does* deliver them
properly) continues to use `{"role": "tool", "tool_call_id": …}`. Unchanged.

## Testing

### Unit tests — new file `tests/backends/test_openai_compat_deepseek.py`

- **`_is_deepseek_family`**
  - True: `deepseek-coder-v2-lite-instruct`, `DeepSeek-V2`, `deepseek-r1:7b`.
  - False: `qwen3.5-coder`, `gemma-3`, empty string, `None`.
- **`_build_deepseek_tools_preamble`**
  - Given a small two-tool list, the returned string contains each tool name,
    contains each tool's description, contains the literal substring
    `<tool_call>` and `</tool_call>`, contains a fragment of each tool's
    parameter JSON schema, and is non-empty.
  - Empty `[]` input returns either `""` or an intent-only string (pick one
    and lock it in the test). The call site already guards on
    `tools_for_model`, so this is just defensive.
- **End-to-end preamble + parser interaction**
  - Build a preamble for a fake `nmap_scan` tool, hand-craft an assistant
    response containing `<tool_call>{"name": "nmap_scan", "arguments":
    {"target": "10.0.0.1"}}</tool_call>` plus prose around it, run it
    through `_extract_text_tool_calls(text, {"nmap_scan"})`, and verify it
    returns `[("nmap_scan", '{"target": "10.0.0.1"}')]`. This is a regression
    guard: it pins the format the preamble teaches to the parser that
    consumes it.

### Smoke test — same file

One integration-style test driving `OpenAICompatBackend.run()` with a
stubbed `AsyncOpenAI` client. The stub is constructed with
`family="deepseek"`. The stub returns a canned `ChatCompletionMessage` with
a `<tool_call>{...}</tool_call>` in `content` and no structured
`tool_calls`. Verifies the `AgentEvent` sequence over one or two turns:

1. `turn` event (turn=1)
2. `tool_call` event with correct `tool_name`, `tool_input`, non-empty
   `tool_use_id`
3. `tool_result` event matching that `tool_use_id`
4. Either another `tool_call` on turn 2 or a `result` event with subtype
   `success`

### Manual verification (out of CI)

A checklist the implementer runs once before declaring done:

1. Start LM Studio with `deepseek-coder-v2-lite-instruct` loaded, OpenAI
   server enabled, context length ≥ 16384.
2. Launch reverser with `--backend lmstudio --model deepseek-coder-v2-lite-instruct`.
3. Run a trivial profile (e.g. `manager` against a known target) for one
   turn.
4. Confirm in the TUI that a tool fires (chip appears, result rendered).
5. Confirm the `<tool_call>…</tool_call>` syntax does not leak into chat
   output.

## Rollback / risk

Additive change. Generic-family models (Claude, Qwen3, Gemma, plain
OpenAI-compatible) hit none of the new code paths. Worst case if the
preamble is somehow counterproductive: the `--model-family generic`
override turns it off entirely.

If the preamble approach turns out not to be sufficient (e.g. the model
needs sampling-parameter tweaks too), the design is structured to absorb
those additions inside `OpenAICompatBackend` without touching callers.

## Documentation

One paragraph added to `README.md` under the local-model section listing
`deepseek-coder-v2-lite-instruct` as a known-good local model. The new
`--model-family` flag appears in `--help` automatically; the README mention
of it is one sentence.

## Open questions / dependencies

- LM Studio version: confirmed unchanged behavior across recent versions
  during manual verification. No specific minimum version requirement
  expected. If a version-sensitive issue surfaces, note it in the manual
  verification checklist.
