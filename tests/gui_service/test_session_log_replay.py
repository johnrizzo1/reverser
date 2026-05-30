"""GET /api/sessions/log/{id}?target=t replays filtered session-log events
for read-only session detail views.
"""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from reverser.gui_service.app import create_app
from reverser.gui_service.config import ServiceConfig


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVERSER_TARGETS_DIR", str(tmp_path / "targets"))
    config = ServiceConfig(host="127.0.0.1", port=0, token="t", project_root=str(tmp_path))
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer t"}


def _write_snapshot(tmp_path, target, session_id, log_relpath):
    """Write a SessionSnapshot JSON that points at log_relpath."""
    target_dir = tmp_path / "targets" / target / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "version": 1,
        "session_id": session_id,
        "target": target,
        "log_path": str(tmp_path / log_relpath),
        "config": {
            "profile": "webpentest", "backend": "claude", "model": None,
            "api_base": None, "budget": 5.0, "max_turns": 50,
        },
        "stats": {"turns": 0, "total_cost": 0.0},
        "state": "stopped",
        "started_at": "2026-05-12T22:54:46Z",
        "stopped_at": "2026-05-12T23:14:00Z",
        "pid": None,
        "conversation": [],
    }
    (target_dir / f"{session_id}.json").write_text(json.dumps(snap))
    return Path(tmp_path / log_relpath)


def _write_snapshot_with_conversation(tmp_path, target, session_id, log_relpath, conversation):
    log_path = _write_snapshot(tmp_path, target, session_id, log_relpath)
    snap_path = tmp_path / "targets" / target / "sessions" / f"{session_id}.json"
    snap = json.loads(snap_path.read_text())
    snap["conversation"] = conversation
    snap_path.write_text(json.dumps(snap))
    return log_path


def _write_log(log_path: Path, entries: list[dict]):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.mark.asyncio
async def test_log_empty_returns_no_events(client, tmp_path):
    log_path = _write_snapshot(tmp_path, "10.10.10.5", "s1", "logs/s1.jsonl")
    _write_log(log_path, [])
    r = await client.get("/api/sessions/log/s1?target=10.10.10.5", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "s1"
    assert body["events"] == []
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_log_filters_to_allowed_kinds(client, tmp_path):
    log_path = _write_snapshot(tmp_path, "t1", "s1", "logs/s1.jsonl")
    _write_log(log_path, [
        {"type": "session_start", "binary": "x"},
        {"type": "turn", "turn": 1},
        {"type": "text", "text": "hi"},
        {"type": "thinking", "text": "Considering options"},
        {"type": "tool_call", "name": "nmap_scan", "input": {"target": "x"}},
        {"type": "tool_result", "content": "open 22/tcp", "is_error": False},
        {"type": "dispatch", "specialty": "ad", "kind": "tool_call",
         "content": "ldap_search"},
        {"type": "result", "subtype": "success"},
        {"type": "session_end"},
    ])
    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    kinds = [e["kind"] for e in body["events"]]
    # text and turn must pass through so read-only replay shows the LLM's
    # assistant responses and buckets per-turn events correctly. Without
    # them the main pane shows only tool chips and everything piles into
    # turn 1.
    assert kinds == [
        "turn", "text", "thinking", "tool_call", "tool_result", "dispatch",
    ]
    e_turn = body["events"][0]
    assert e_turn["turn"] == 1
    e_text = body["events"][1]
    assert e_text["content"] == "hi"
    e_thinking = body["events"][2]
    assert e_thinking["content"] == "Considering options"
    e_tc = body["events"][3]
    assert e_tc["name"] == "nmap_scan"
    assert isinstance(e_tc["input"], str)  # serialized for the frontend
    e_tr = body["events"][4]
    assert e_tr["ok"] is True
    assert e_tr["preview"] == "open 22/tcp"
    e_dispatch = body["events"][5]
    assert e_dispatch["specialty"] == "ad"
    assert e_dispatch["phase"] == "tool_call"


@pytest.mark.asyncio
async def test_log_replay_includes_user_messages_from_snapshot_conversation(client, tmp_path):
    log_path = _write_snapshot_with_conversation(
        tmp_path,
        "t1",
        "s1",
        "logs/s1.jsonl",
        [{
            "user": "please decode the flag",
            "agent": "working",
            "turn": 1,
            "timestamp": "2026-05-29T10:00:00Z",
            "cost": 0.0,
            "events": [],
        }],
    )
    _write_log(log_path, [
        {"type": "turn", "turn": 1},
        {"type": "text", "text": "I will decode it."},
    ])

    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["events"][0] == {
        "kind": "user",
        "turn": 1,
        "content": "please decode the flag",
        "ts": "2026-05-29T10:00:00Z",
    }


@pytest.mark.asyncio
async def test_log_truncates_above_5000_cap(client, tmp_path):
    log_path = _write_snapshot(tmp_path, "t1", "s1", "logs/s1.jsonl")
    entries = [{"type": "thinking", "text": f"thought {i}"} for i in range(6000)]
    _write_log(log_path, entries)

    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert len(body["events"]) == 5000
    # Latest 5000 kept (head dropped):
    assert body["events"][0]["content"] == "thought 1000"
    assert body["events"][-1]["content"] == "thought 5999"


@pytest.mark.asyncio
async def test_log_404_missing_snapshot(client):
    r = await client.get("/api/sessions/log/no-such-session?target=t1", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_log_404_missing_log_file(client, tmp_path):
    """Snapshot points at a log path that doesn't exist on disk."""
    _write_snapshot(tmp_path, "t1", "s1", "logs/missing.jsonl")
    # Don't create the log file.
    r = await client.get("/api/sessions/log/s1?target=t1", headers=HEADERS)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_log_422_missing_target_query(client):
    r = await client.get("/api/sessions/log/s1", headers=HEADERS)
    assert r.status_code == 422
