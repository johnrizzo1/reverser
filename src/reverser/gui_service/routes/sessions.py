"""POST/GET /api/sessions and its sub-resources."""
import json
import os

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from ...session_log import load_session_log
from ...sessions import SessionNotFoundError, SessionStateError
from ...sessions import delete as delete_snapshot
from ...sessions import load as load_snapshot
from ...sessions import set_archived as set_snapshot_archived

router = APIRouter()


class CreateSession(BaseModel):
    target: str
    profile: str
    backend: str
    model: str | None = None
    api_base: str | None = None
    budget: float = 5.0
    max_turns: int = 50


class MessageBody(BaseModel):
    text: str


class BudgetBody(BaseModel):
    budget: float | None = None
    max_turns: int | None = None


class SudoBody(BaseModel):
    password: str


def _manager(request: Request):
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is None:
        raise HTTPException(500, detail="session_manager not configured")
    return mgr


@router.get("/api/sessions")
def list_sessions(request: Request) -> dict:
    return {"sessions": _manager(request).list_sessions()}


@router.post("/api/sessions")
async def create_session(request: Request, body: CreateSession) -> dict:
    try:
        return await _manager(request).create_session(
            target=body.target,
            profile_key=body.profile,
            backend_name=body.backend,
            model=body.model,
            api_base=body.api_base,
            budget=body.budget,
            max_turns=body.max_turns,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/api/sessions/{session_id}/messages", status_code=204)
async def send_message(request: Request, session_id: str, body: MessageBody) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    await gs.send_message(body.text)
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/skills/{skill_key}", status_code=204)
async def trigger_skill(request: Request, session_id: str, skill_key: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    try:
        await gs.trigger_skill(skill_key)
    except KeyError as e:
        raise HTTPException(404, detail=str(e))
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/stop", status_code=204)
async def stop_session(request: Request, session_id: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.stop()
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/done", status_code=204)
async def mark_done(request: Request, session_id: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.mark_completed()
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/resume")
async def resume_session(request: Request, session_id: str) -> dict:
    mgr = _manager(request)
    # Find the session's target by scanning list_sessions
    rows = mgr.list_sessions()
    row = next((r for r in rows if r["id"] == session_id), None)
    if row is None:
        raise HTTPException(404)
    try:
        return await mgr.resume_session(
            snapshot_id=session_id,
            target=row["target"],
            backend_name=None, model=None, api_base=None,
        )
    except KeyError:
        raise HTTPException(404)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/api/sessions/{session_id}/budget", status_code=204)
async def update_budget(request: Request, session_id: str, body: BudgetBody) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.update_budget(body.budget, body.max_turns)
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/sudo", status_code=204)
async def set_sudo(request: Request, session_id: str, body: SudoBody) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    gs.set_sudo(body.password)
    return Response(status_code=204)


@router.get("/api/sessions/conversation/{session_id}")
def get_conversation(session_id: str, target: str) -> dict:
    """Return a snapshot's conversation history for read-only replay.

    `target` is required because reverser.sessions.load takes both args
    (sessions are scoped per target). The frontend knows the target from
    the SessionsPanel row it clicked.
    """
    try:
        snap = load_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404, detail=f"unknown session: {session_id!r}")
    return {
        "id": snap.session_id,
        "target": snap.target,
        "profile": snap.config.profile,
        "state": snap.state,
        "conversation": [
            {
                "user": e.user,
                "agent": e.agent,
                "turn": e.turn,
                "timestamp": e.timestamp,
                "cost": e.cost,
            }
            for e in snap.conversation
        ],
    }


@router.get("/api/sessions/log/{session_id}")
def get_session_log(session_id: str, target: str) -> dict:
    """Return filtered session-log events for read-only chat/timeline replay.

    Filters to {thinking, tool_call, tool_result, dispatch}. Caps at 5000
    events (oldest dropped).
    """
    try:
        snap = load_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404, detail=f"unknown session: {session_id!r}")

    log_path = snap.log_path
    if not log_path or not os.path.isfile(log_path):
        raise HTTPException(404, detail=f"session log file not found: {log_path!r}")

    raw = load_session_log(log_path)
    _ALLOWED = {"thinking", "tool_call", "tool_result", "dispatch"}

    out: list[dict] = []
    for entry in raw:
        kind = entry.get("type")
        if kind not in _ALLOWED:
            continue
        ts = entry.get("ts")
        if kind == "thinking":
            out.append({"kind": "thinking", "content": entry.get("text", ""), "ts": ts})
        elif kind == "tool_call":
            input_val = entry.get("input")
            if input_val is None:
                input_str = ""
            elif isinstance(input_val, str):
                input_str = input_val
            else:
                input_str = json.dumps(input_val)
            out.append({
                "kind": "tool_call",
                "name": entry.get("name", ""),
                "input": input_str,
                "ts": ts,
            })
        elif kind == "tool_result":
            out.append({
                "kind": "tool_result",
                "ok": not entry.get("is_error", False),
                "preview": (entry.get("content") or "")[:4096],
                "ts": ts,
            })
        elif kind == "dispatch":
            out.append({
                "kind": "dispatch",
                "specialty": entry.get("specialty", ""),
                "phase": entry.get("kind", ""),
                "content": entry.get("content", ""),
                "ts": ts,
            })

    truncated = len(out) > 5000
    if truncated:
        out = out[-5000:]

    return {"id": session_id, "events": out, "truncated": truncated}


@router.post("/api/sessions/{session_id}/archive", status_code=204)
def archive_session(request: Request, session_id: str, target: str) -> Response:
    # Check the in-memory active session first — the on-disk snapshot may
    # not reflect the running state yet.
    mgr = _manager(request)
    if mgr.active is not None and mgr.active.session_id == session_id:
        raise HTTPException(409, detail="session is active; stop it first")
    try:
        set_snapshot_archived(target, session_id, True)
    except SessionNotFoundError:
        raise HTTPException(404)
    except SessionStateError as e:
        raise HTTPException(409, detail=str(e))
    return Response(status_code=204)


@router.delete("/api/sessions/{session_id}/archive", status_code=204)
def unarchive_session(session_id: str, target: str) -> Response:
    try:
        set_snapshot_archived(target, session_id, False)
    except SessionNotFoundError:
        raise HTTPException(404)
    return Response(status_code=204)


@router.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(request: Request, session_id: str, target: str) -> Response:
    mgr = _manager(request)
    if mgr.active is not None and mgr.active.session_id == session_id:
        raise HTTPException(409, detail="session is active; stop it first")
    try:
        delete_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404)
    except SessionStateError as e:
        raise HTTPException(409, detail=str(e))
    return Response(status_code=204)
