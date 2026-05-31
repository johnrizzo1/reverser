import pytest
from types import SimpleNamespace

import reverser.backends.claude as claude_mod
from reverser.backends.claude import ClaudeBackend


@pytest.fixture(autouse=True)
def _stub_sdk(monkeypatch):
    monkeypatch.setattr(claude_mod, "create_sdk_mcp_server", lambda **kw: object())


def _fake_query(messages):
    async def _q(*, prompt, options):
        for m in messages:
            yield m
    return _q


@pytest.mark.asyncio
async def test_rate_limit_rejected_emits_quota_exhausted(monkeypatch):
    rl = SimpleNamespace(rate_limit_info=SimpleNamespace(status="rejected"))
    monkeypatch.setattr(claude_mod, "query", _fake_query([rl]))
    be = ClaudeBackend(tools=[])
    events = [e async for e in be.run(prompt="hi", system_prompt="s")]
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "quota_exhausted"


@pytest.mark.asyncio
async def test_query_exception_surfaces_as_api_error(monkeypatch):
    async def _boom(*, prompt, options):
        raise RuntimeError("subprocess died")
        yield  # pragma: no cover — make it an async generator
    monkeypatch.setattr(claude_mod, "query", _boom)
    be = ClaudeBackend(tools=[])
    events = [e async for e in be.run(prompt="hi", system_prompt="s")]  # must NOT raise
    errs = [e for e in events if e.kind == "error"]
    assert errs and errs[-1].subtype == "api_error"
    assert "subprocess died" in errs[-1].content


@pytest.mark.asyncio
async def test_rate_limit_warning_is_non_fatal(monkeypatch):
    warn = SimpleNamespace(rate_limit_info=SimpleNamespace(status="allowed_warning"))
    monkeypatch.setattr(claude_mod, "query", _fake_query([warn]))
    be = ClaudeBackend(tools=[])
    events = [e async for e in be.run(prompt="hi", system_prompt="s")]
    assert any(e.kind == "llm_status" for e in events)   # heads-up emitted
    assert not any(e.kind == "error" for e in events)    # warning is not an error
