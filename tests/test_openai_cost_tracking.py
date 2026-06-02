"""Per-turn token accounting and cost fields on result events.

Tests that OpenAICompatBackend populates tokens/cost on result events,
using usage chunks when available and falling back to char-based estimates.
"""
import pytest
from types import SimpleNamespace

from reverser.backends.openai_compat import OpenAICompatBackend


def _chunk(content=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, reasoning=None, reasoning_content=None, tool_calls=None)
    choices = [] if (content is None and finish is None) else [
        SimpleNamespace(delta=delta, finish_reason=finish, index=0)]
    return SimpleNamespace(choices=choices, usage=usage)


def _make_stream(chunks):
    async def _agen():
        for c in chunks:
            yield c
    return _agen()


def _backend(create_fn, *, token_cost_per_1k=0.0):
    be = OpenAICompatBackend(tools=[], model="m", api_base="http://x/v1",
                             token_cost_per_1k=token_cost_per_1k)
    be._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))
    return be


@pytest.mark.asyncio
async def test_tokens_from_usage_reported():
    async def create(**kw):
        return _make_stream([
            _chunk(content="this is a sufficiently long answer to be treated as done " * 3,
                   finish="stop"),
            _chunk(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40)),
        ])
    be = _backend(create, token_cost_per_1k=2.0)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    result = [e for e in events if e.kind == "result"][-1]
    assert result.tokens == 140
    assert result.cost == pytest.approx(140 / 1000 * 2.0)


@pytest.mark.asyncio
async def test_tokens_estimated_when_no_usage():
    text = "x" * 400  # ~100 tokens generated
    async def create(**kw):
        return _make_stream([_chunk(content=text, finish="stop")])
    be = _backend(create, token_cost_per_1k=0.0)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    result = [e for e in events if e.kind == "result"][-1]
    assert result.tokens >= 100      # generated ~100 + prompt
    assert result.cost == 0.0        # rate 0 -> no dollars, tokens still tracked


@pytest.mark.asyncio
async def test_cost_zero_rate_tokens_still_tracked():
    async def create(**kw):
        return _make_stream([
            _chunk(content="long enough answer " * 5, finish="stop"),
            _chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)),
        ])
    be = _backend(create, token_cost_per_1k=0.0)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    result = [e for e in events if e.kind == "result"][-1]
    assert result.tokens == 15 and result.cost == 0.0
