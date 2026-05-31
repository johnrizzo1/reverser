"""SessionLog persists dispatch_specialist sub-agent events.

The TUI surfaces dispatch events live via emit_dispatch_event; this
parallel write to the JSONL log lets read-only session replay show them
after the fact.
"""
import json

import pytest

from reverser.session_log import SessionLog, load_session_log


def test_log_dispatch_event_writes_expected_shape(tmp_path):
    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    try:
        slog.log_dispatch_event("ad", "tool_call", "ldap_search cn=Users,dc=corp")
    finally:
        slog.close()

    entries = load_session_log(str(log_path))
    dispatch_entries = [e for e in entries if e.get("type") == "dispatch"]
    assert len(dispatch_entries) == 1
    e = dispatch_entries[0]
    assert e["specialty"] == "ad"
    assert e["kind"] == "tool_call"
    assert e["content"] == "ldap_search cn=Users,dc=corp"
    assert "ts" in e


def test_log_dispatch_event_truncates_content(tmp_path):
    """Content is capped to keep log files manageable."""
    log_path = tmp_path / "test.jsonl"
    slog = SessionLog(str(log_path))
    try:
        slog.log_dispatch_event("ad", "tool_result", "x" * 5000)
    finally:
        slog.close()

    entries = load_session_log(str(log_path))
    e = next(e for e in entries if e["type"] == "dispatch")
    assert len(e["content"]) <= 4096


def test_dispatch_start_and_end_persisted_to_log(monkeypatch, tmp_path):
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path))
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    import json
    import asyncio
    from unittest.mock import patch
    import reverser.kb
    reverser.kb._kb_cache.clear()
    from reverser.profiles import get_profile
    from reverser.tui.session import AgentSession
    from reverser.tools.dispatch import dispatch_specialist
    from reverser.sessions import current_session

    sess = AgentSession(binary_path="10.10.10.5", profile=get_profile("manager"))
    current_session.set(sess)

    async def ok_query(prompt, options):
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
        yield AssistantMessage(content=[TextBlock(text="### Hypothesis outcome\nINCONCLUSIVE")], model="claude")
        yield ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0,
                             is_error=False, num_turns=1, session_id="t",
                             total_cost_usd=0.0, result="x")

    fn = getattr(dispatch_specialist, "handler", None) or getattr(dispatch_specialist, "fn", None) or dispatch_specialist
    with patch("reverser.tools.dispatch.query", ok_query):
        asyncio.new_event_loop().run_until_complete(
            fn({"specialty": "ad", "sub_goal": "s", "target": "10.10.10.5", "hypothesis_id": 1})
        )
    sess._slog._f.flush()
    records = [json.loads(l) for l in open(sess._slog.path) if l.strip()]
    kinds = [(r.get("type"), r.get("kind")) for r in records]
    assert ("dispatch", "start") in kinds
    assert ("dispatch", "end") in kinds
