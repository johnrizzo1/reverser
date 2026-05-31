import pytest
from types import SimpleNamespace

from reverser.backends.openai_compat import OpenAICompatBackend


class _RateLimit(Exception):
    status_code = 429

class _Auth(Exception):
    status_code = 401


def _make_backend(create_fn):
    be = OpenAICompatBackend(tools=[], model="m", api_base="http://x/v1")
    be._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn))
    )
    return be


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def _nosleep(_):
        return None
    import reverser.backends.retry as r
    monkeypatch.setattr(r.asyncio, "sleep", _nosleep)


@pytest.mark.asyncio
async def test_persistent_rate_limit_emits_rate_limited():
    async def create(**kw):
        raise _RateLimit()
    be = _make_backend(create)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "rate_limited"
    retry_notes = [e for e in events if e.kind == "llm_status" and "retry" in e.content.lower()]
    assert len(retry_notes) == 3


@pytest.mark.asyncio
async def test_auth_error_is_terminal_no_retry():
    calls = {"n": 0}
    async def create(**kw):
        calls["n"] += 1
        raise _Auth()
    be = _make_backend(create)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    assert calls["n"] == 1
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "quota_exhausted"
    assert not any(e.kind == "llm_status" and "retry" in e.content.lower() for e in events)


@pytest.mark.asyncio
async def test_context_window_error_terminal_with_help():
    calls = {"n": 0}
    async def create(**kw):
        calls["n"] += 1
        raise Exception("n_keep n_ctx mismatch")
    be = _make_backend(create)
    events = [e async for e in be.run(prompt="hi", system_prompt="s", max_turns=1)]
    assert calls["n"] == 1
    errs = [e for e in events if e.kind == "error"]
    assert errs and "Context Length" in errs[-1].content
