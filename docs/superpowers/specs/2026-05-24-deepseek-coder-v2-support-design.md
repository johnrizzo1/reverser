# DeepSeek-Coder-V2 (Lite) Support — Design

**Status:** Draft
**Date:** 2026-05-24
**Owner:** John Rizzo
**Target model:** `deepseek-coder-v2-lite-instruct` (16B MoE, 2.4B active), via LM Studio GGUF.

## Problem

Today, running DeepSeek-Coder-V2-Lite-Instruct through `OpenAICompatBackend`
against an LM Studio endpoint fails to drive tool calls. The model narrates
what it would do ("I'll call `nmap_scan` now…") but no `tool_call` event ever
fires.

Root cause: LM Studio's GGUF chat template for DeepSeek-Coder-V2 does not
render the OpenAI `tools` array (passed via the chat-completions request) into
DeepSeek's native tool-calling format inside the prompt. The model therefore
never sees that tools are available, what their schemas look like, or how to
call them. It defaults to prose.

The user also reported that "thinking" looks broken. DeepSeek-Coder-V2-Lite
is **not** a reasoning model — it has no `reasoning_content` field and no
`<think>` blocks. Apparent "thinking" is the model improvising a plan in
plain text before (failing to) emit a tool call. Once tool calling works,
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
- Refactoring the existing Qwen3 / Gemma parser logic into a `ModelFamilyAdapter`
  interface. That's a worthwhile cleanup but explicitly deferred — this change
  follows the existing per-family-regex pattern.
- Tweaking sampling parameters. DeepSeek's docs recommend `temperature=0.0`
  for tool calling, but the current backend uses model defaults across the
  board and we keep that consistent here.
- Native-API DeepSeek support via `api.deepseek.com`. The official API
  already delivers structured `tool_calls` correctly via the existing
  OpenAI-compatible path; this design targets the LM Studio gap.

## Architecture

All changes live in `src/reverser/backends/openai_compat.py` alongside the
existing per-family logic (Qwen3, Gemma). One CLI argument is added in
`src/reverser/cli.py`. No new files in the backend; tests go in a new file.

Three axes:

1. **Detection** — recognize the DeepSeek family at backend construction.
2. **Prompt augmentation** — append a tools-preamble to the system prompt
   for DeepSeek models so the model sees the tool list and the expected
   wire format.
3. **Output parsing** — extract tool calls from DeepSeek's native marker
   syntax when the model emits it as plain text.

### Detection

New module-level helper:

```python
def _is_deepseek_family(model: str) -> bool:
    return "deepseek" in (model or "").lower()
```

Covers `deepseek-coder-v2-lite-instruct`, `deepseek-coder-v2`, `deepseek-v2`,
`deepseek-v2.5`, `deepseek-r1` and any future variant that keeps the
`deepseek` substring. All these share the same `<｜tool▁call▁begin｜>` marker
family in their official chat templates.

Note on R1: matching R1 here means R1 users get the DeepSeek tool-calling
preamble and parser, which is correct — R1 uses the same marker syntax.
It does **not** mean we now display R1's `reasoning_content` as a thinking
stream; that's still a separate, deferred task per Non-goals.

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
   model can copy it:
   ```
   <｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>TOOL_NAME
   ```json
   {"arg": "value"}
   ```
   <｜tool▁call▁end｜><｜tool▁calls▁end｜>
   ```
4. A trailing rule: *"Emit the markers verbatim. After a tool result,
   continue with another tool call or a final answer."*

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

**Verification dependency.** Before implementation, pull
`tokenizer_config.json` from the official
[`deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct`](https://huggingface.co/deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct)
Hugging Face repo and confirm the exact bytes of the marker tokens. The
markers use FULLWIDTH VERTICAL LINE (U+FF5C) and LOWER ONE EIGHTH BLOCK
(U+2581) — easy to mistype as ASCII `|` and underscore. The implementation
plan must verify these bytes match before shipping the preamble.

### Output parser

New module-level helper and pattern:

```python
_DEEPSEEK_TOOL_CALL_PATTERN = re.compile(
    r'<｜tool▁call▁begin｜>\s*function\s*<｜tool▁sep｜>\s*'
    r'(?P<name>[A-Za-z_]\w*)\s*'
    r'```(?:json)?\s*(?P<args>\{.*?\})\s*```\s*'
    r'<｜tool▁call▁end｜>',
    re.DOTALL,
)

def _parse_deepseek_tool_calls(
    text: str, known_tools: set[str]
) -> list[tuple[str, str]]: ...
```

Matches each individual `<｜tool▁call▁begin｜>…<｜tool▁call▁end｜>` block,
whether or not it's wrapped in the outer `<｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>`
envelope (some templates omit the envelope). For each match:

- Validate `name` is in `known_tools`. Skip if not.
- `json.loads(args)`. Skip on `JSONDecodeError`.
- Append `(name, args_json_string)`.

Returns the same shape as `_parse_qwen3_xml_calls` and `_parse_gemma_tool_calls`,
so it slots into the existing cascade.

**Wire-up.** `_extract_text_tool_calls` gains a `family` parameter
(default `"generic"`). When `family == "deepseek"`, try the DeepSeek parser
first, then fall through to the existing Gemma → Qwen3 → JSON cascade. For
`family == "generic"`, the DeepSeek parser is skipped entirely. The single
caller in `OpenAICompatBackend.run()` passes `self._family`.

### Visible-text scrubbing

The existing `display_content` cleanup strips `<tool_call>…</tool_call>` and
Gemma's `<|tool_call>…<tool_call|>` so they don't leak into the chat UI.
Add two more `re.sub` calls to strip:

1. The outer `<｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>` envelope.
2. Any stray individual `<｜tool▁call▁begin｜>…<｜tool▁call▁end｜>` blocks.

Both inserted right before the `if visible_text:` emit check, only when
`self._family == "deepseek"` (the markers are inert characters for other
families, but skipping the regex avoids needless work).

### Tool-result framing

The existing text-extracted-tool-call path feeds results back as a `user`
message containing `"[Tool result: NAME — OK|ERROR]\n<result>"` plus a
"continue your analysis" nudge. That keeps working for DeepSeek as-is.

We deliberately do **not** wrap results in DeepSeek's
`<｜tool▁outputs▁begin｜>…<｜tool▁outputs▁end｜>` markers. Those tokens are
meaningful at the chat-template tokenization layer — when the engine
tokenizes a `role=tool` message into the prompt. Hand-writing them inside a
`user` message would have them treated as literal text by LM Studio, which
is worse than plain prose.

The structured `tool_calls` path (when the engine *does* deliver them
properly) continues to use `{"role": "tool", "tool_call_id": …}`. Unchanged.

## Testing

### Unit tests — new file `tests/backends/test_openai_compat_deepseek.py`

- **`_is_deepseek_family`**
  - True: `deepseek-coder-v2-lite-instruct`, `DeepSeek-V2`, `deepseek-r1:7b`.
  - False: `qwen3.5-coder`, `gemma-3`, empty string, `None`.
- **`_build_deepseek_tools_preamble`**
  - Given a small two-tool list, the returned string contains each tool
    name, contains the literal `<｜tool▁call▁begin｜>function<｜tool▁sep｜>`
    sequence, contains a JSON-schema fragment for the parameters, and is
    non-empty.
- **`_parse_deepseek_tool_calls`**
  - Single call wrapped in outer envelope.
  - Single call with no outer envelope.
  - Multi-call inside one envelope.
  - Unknown tool name → skipped.
  - Malformed JSON → skipped.
  - Empty input → `[]`.
- **`_extract_text_tool_calls` cascade**
  - `family="deepseek"` with DeepSeek markers present → DeepSeek parser wins.
  - `family="deepseek"` with no markers → falls through to Gemma / Qwen3 /
    JSON parsers (existing behavior).
  - `family="generic"` with DeepSeek markers present → DeepSeek parser is
    skipped (locks in the family-gated behavior).
- **Visible-text scrubbing**
  - Assistant content `"prose <｜tool▁call▁begin｜>…<｜tool▁call▁end｜> more prose"`
    under `family="deepseek"` → markers stripped, prose preserved.

### Smoke test — same file

One integration-style test driving `OpenAICompatBackend.run()` with a
stubbed `AsyncOpenAI` client. The stub returns a canned
`ChatCompletionMessage` with DeepSeek markers in `content` and no structured
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
5. Confirm no `<｜…｜>` markers leak into chat output.

## Rollback / risk

Additive change. Generic-family models (Claude, Qwen3, Gemma, plain
OpenAI-compatible) hit none of the new code paths. Worst case if the
DeepSeek regex misfires: a tool call gets skipped and the existing
"nudge the model to use tools" branch kicks in — same as today's behavior
for an unrecognized format.

If the preamble approach turns out not to be sufficient (e.g. the model
needs sampling-parameter tweaks too), the parser and visible-text scrubbing
are still independently useful and can stay.

## Documentation

One paragraph added to `README.md` under the local-model section listing
`deepseek-coder-v2-lite-instruct` as a known-good local model. The new
`--model-family` flag appears in `--help` automatically; the README mention
of it is one sentence.

## Open questions / dependencies

- The exact bytes of the DeepSeek marker tokens must be confirmed against
  `tokenizer_config.json` from the official HF repo before the preamble or
  parser regex ships. This belongs in the implementation plan.
- LM Studio version matters: older LM Studio releases may strip / mangle
  the wide-Unicode markers in their UI but pass them through correctly over
  the OpenAI server. If the smoke test surfaces a mangling issue, add an
  ASCII-fallback regex pass before declaring done.
