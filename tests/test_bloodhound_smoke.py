"""Opt-in integration smoke test for the BloodHound stack.

Skipped unless `neo4j` is on PATH AND `REVERSER_BLOODHOUND_SMOKE=1`.
This test actually starts and stops a real per-target Neo4j instance.
"""

import asyncio
import os
import shutil

import pytest

from reverser.tools.bloodhound import (
    bloodhound_start,
    bloodhound_stop,
    bloodhound_status,
    _read_pid,
    _process_alive,
)


def _neo4j_available() -> bool:
    return (
        shutil.which("neo4j") is not None
        and os.environ.get("REVERSER_BLOODHOUND_SMOKE") == "1"
    )


pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Real Neo4j smoke test gated on REVERSER_BLOODHOUND_SMOKE=1 + neo4j in PATH",
)


def _call(tool_obj, args):
    fn = getattr(tool_obj, "handler", None) or getattr(tool_obj, "fn", None) or tool_obj
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn(args))
    finally:
        loop.close()


def test_smoke_start_status_stop(tmp_targets_dir, monkeypatch):
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")
    target = "smoke.target.test"

    start_result = _call(bloodhound_start, {"target": target})
    assert start_result.get("is_error") is not True, start_result["content"][0]["text"]
    pid = _read_pid(target)
    assert pid is not None and _process_alive(pid)

    try:
        status_result = _call(bloodhound_status, {"target": target})
        text = status_result["content"][0]["text"]
        assert "RUNNING" in text
        assert "Users" in text
    finally:
        stop_result = _call(bloodhound_stop, {"target": target})
        assert stop_result.get("is_error") is not True
        assert _read_pid(target) is None
