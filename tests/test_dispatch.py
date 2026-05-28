"""Tests for the dispatch_specialist tool with mocked SDK."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from reverser.backends.base import AgentEvent


@pytest.fixture(autouse=True)
def reset_current_session():
    from reverser.sessions import current_session
    token = current_session.set(None)
    try:
        yield
    finally:
        current_session.reset(token)


def _call_tool(tool_obj, args):
    """Invoke an SDK tool object's underlying coroutine handler."""
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    return asyncio.new_event_loop().run_until_complete(fn(args))


def _mock_query(report_text: str, cost: float = 0.10, turns: int = 5):
    """Build an async generator that mimics claude_agent_sdk.query."""
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage

    async def gen(prompt, options):
        yield AssistantMessage(content=[TextBlock(text=report_text)], model="claude")
        yield ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=turns,
            session_id="test-session",
            total_cost_usd=cost,
            result=report_text,
        )
    return gen


def test_dispatch_specialist_returns_report_and_outcome(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.tools.dispatch import dispatch_specialist

    report = """### TL;DR
Confirmed SMB signing is off.

### Findings
- DC at 10.10.10.5 has signing=False

### Hypothesis outcome
CONFIRMED — verified via nxc smb output.

### KB writes
- Added finding #1

### Suggested follow-up
Test NTLM relay viability."""

    with patch("reverser.tools.dispatch.query", _mock_query(report, cost=0.12, turns=4)):
        result = _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "Verify SMB signing on DC",
            "target": "10.10.10.5",
        })

    text = result["content"][0]["text"]
    assert "CONFIRMED" in text or "confirmed" in text
    assert "10.10.10.5" in text or "Confirmed" in text
    # Structured fields should appear in the surface
    assert "Cost" in text and "0.12" in text
    assert "Turns" in text and "4" in text
    assert "outcome" in text.lower()


def test_dispatch_specialist_unknown_specialty_returns_error(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    from reverser.tools.dispatch import dispatch_specialist
    result = _call_tool(dispatch_specialist, {
        "specialty": "nonexistent",
        "sub_goal": "x",
        "target": "10.10.10.5",
    })
    text = result["content"][0]["text"]
    assert "unknown" in text.lower() or "invalid" in text.lower()


def test_dispatch_specialist_handles_sdk_error(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.tools.dispatch import dispatch_specialist

    async def fail_query(prompt, options):
        raise RuntimeError("SDK exploded")
        yield  # pragma: no cover -- makes it an async generator

    with patch("reverser.tools.dispatch.query", fail_query):
        result = _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
        })

    text = result["content"][0]["text"]
    assert "error" in text.lower()
    assert "SDK exploded" in text


def test_dispatch_specialist_strips_dispatch_tool_from_subagent_allowed_tools(monkeypatch, tmp_path):
    """The sub-agent must NOT have access to dispatch_specialist (no recursive dispatch)."""
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.tools.dispatch import dispatch_specialist

    captured = {}

    async def capturing_query(prompt, options):
        captured["allowed_tools"] = list(options.allowed_tools)
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")], model="claude")
        yield ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
            result="x",
        )

    with patch("reverser.tools.dispatch.query", capturing_query):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
        })

    assert "allowed_tools" in captured
    allowed = captured["allowed_tools"]
    # The sub-agent's allowed-tools list must not contain dispatch_specialist
    assert "mcp__re__dispatch_specialist" not in allowed
    # Some other expected tool should be present
    assert any("kb_show" in t for t in allowed) or any("nmap" in t for t in allowed)


def test_dispatch_specialist_increments_dispatch_count(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.kb.store import KB

    kb = KB("10.10.10.5")
    h = kb.add_hypothesis(statement="test")

    report = "### Hypothesis outcome\nCONFIRMED"
    with patch("reverser.tools.dispatch.query", _mock_query(report)):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
            "hypothesis_id": h.id,
        })

    fetched = kb.get_hypothesis(h.id)
    assert fetched.dispatch_count == 1
    assert fetched.dispatched_to == "ad"


def test_dispatch_specialist_emits_hypothesis_update(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.kb.store import KB
    from reverser.sessions import current_session
    from reverser.tools.dispatch import dispatch_specialist

    kb = KB("10.10.10.5")
    h = kb.add_hypothesis(statement="test")
    sess = SimpleNamespace(
        _snapshot=SimpleNamespace(
            config=SimpleNamespace(backend="claude", model=None, api_base=None),
            in_flight=None,
        ),
        _slog=SimpleNamespace(log_dispatch_event=MagicMock()),
        emit_dispatch_event=MagicMock(),
        emit_kb_event=MagicMock(),
    )
    current_session.set(sess)

    report = "### Hypothesis outcome\nCONFIRMED"
    with patch("reverser.tools.dispatch.query", _mock_query(report)):
        _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "x",
            "target": "10.10.10.5",
            "hypothesis_id": h.id,
        })

    hypothesis_calls = [
        call.args for call in sess.emit_kb_event.call_args_list
        if call.args and call.args[0] == "hypothesis"
    ]
    assert hypothesis_calls
    _, payload = hypothesis_calls[0]
    assert payload["action"] == "update"
    assert payload["row"]["id"] == h.id
    assert payload["row"]["status"] == "testing"
    assert payload["row"]["dispatch_count"] == 1
    assert payload["row"]["dispatched_to"] == "ad"


def test_dispatch_specialist_uses_active_local_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import current_session
    from reverser.tools.dispatch import dispatch_specialist

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
        backend_name="lmstudio",
        model="local-model",
        api_base="http://127.0.0.1:1234/v1",
    )
    current_session.set(sess)

    captured = {}

    class FakeBackend:
        async def run(self, prompt, system_prompt, **kwargs):
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            captured["kwargs"] = kwargs
            yield AgentEvent(kind="turn", turns=1, turn=1)
            yield AgentEvent(kind="text", content="### Hypothesis outcome\nCONFIRMED")
            yield AgentEvent(kind="result", subtype="success", turns=1)

    def fake_create_backend(name, tools, **kwargs):
        captured["backend_name"] = name
        captured["backend_kwargs"] = kwargs
        return FakeBackend()

    with patch("reverser.tools.dispatch.create_backend", fake_create_backend), \
         patch("reverser.tools.dispatch.query") as claude_query:
        result = _call_tool(dispatch_specialist, {
            "specialty": "ad",
            "sub_goal": "Verify SMB signing",
            "target": "10.10.10.5",
        })

    assert captured["backend_name"] == "lmstudio"
    assert captured["backend_kwargs"]["model"] == "local-model"
    assert captured["backend_kwargs"]["api_base"] == "http://127.0.0.1:1234/v1"
    assert "Dispatch context" in captured["system_prompt"]
    assert captured["kwargs"]["allowed_tools"]
    assert "mcp__re__dispatch_specialist" not in captured["kwargs"]["allowed_tools"]
    claude_query.assert_not_called()
    assert "CONFIRMED" in result["content"][0]["text"]


def test_dispatch_specialist_serializes_local_backend_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import reverser.kb
    reverser.kb._kb_cache.clear()

    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.sessions import current_session
    from reverser.tools.dispatch import dispatch_specialist

    sess = AgentSession(
        binary_path="10.10.10.5",
        profile=get_profile("manager"),
        backend_name="lmstudio",
        model="local-model",
    )
    current_session.set(sess)

    counters = {"active": 0, "max_active": 0}

    class SlowBackend:
        async def run(self, prompt, system_prompt, **kwargs):
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
            try:
                await asyncio.sleep(0.02)
                yield AgentEvent(kind="turn", turns=1, turn=1)
                yield AgentEvent(kind="text", content="### Hypothesis outcome\nINCONCLUSIVE")
                yield AgentEvent(kind="result", subtype="success", turns=1)
            finally:
                counters["active"] -= 1

    async def run_two_dispatches():
        fn = getattr(dispatch_specialist, "handler", None) or dispatch_specialist
        await asyncio.gather(
            fn({"specialty": "ad", "sub_goal": "one", "target": "10.10.10.5"}),
            fn({"specialty": "pentest", "sub_goal": "two", "target": "10.10.10.5"}),
        )

    with patch("reverser.tools.dispatch.create_backend", return_value=SlowBackend()), \
         patch("reverser.tools.dispatch.query") as claude_query:
        asyncio.new_event_loop().run_until_complete(run_two_dispatches())

    assert counters["max_active"] == 1
    claude_query.assert_not_called()


def test_exploit_in_dispatchable_specialties():
    from reverser.tools.dispatch import _DISPATCHABLE_SPECIALTIES
    assert "exploit" in _DISPATCHABLE_SPECIALTIES


def test_dispatchable_specialties_count_after_exploit():
    from reverser.tools.dispatch import _DISPATCHABLE_SPECIALTIES
    assert len(_DISPATCHABLE_SPECIALTIES) == 6
