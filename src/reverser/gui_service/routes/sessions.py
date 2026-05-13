"""POST/GET /api/sessions and its sub-resources."""
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

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
