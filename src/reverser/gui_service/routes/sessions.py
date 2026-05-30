"""POST/GET /api/sessions and its sub-resources."""
import json
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, model_validator

from ...session_log import load_session_log
from ...sessions import SessionNotFoundError, SessionStateError, is_session_alive
from ...sessions import delete as delete_snapshot
from ...sessions import load as load_snapshot
from ...sessions import save as save_snapshot
from ...sessions import set_archived as set_snapshot_archived
from .backends import _BACKENDS as _BACKENDS_META

router = APIRouter()

# Single source of truth for valid backend keys; matches the static metadata
# served by GET /api/backends.
_VALID_BACKENDS = frozenset(b["key"] for b in _BACKENDS_META)
_BACKENDS_REQUIRING_MODEL = frozenset(
    b["key"] for b in _BACKENDS_META if b["requires_model"]
)


class CreateSession(BaseModel):
    # Legacy field: raw address / binary path. Optional when target_name is given.
    target: Optional[str] = None
    # New fields (Task 29): resolve by named target + optional address override.
    target_name: Optional[str] = None
    address: Optional[str] = None
    profile: str
    backend: str
    model: str | None = None
    api_base: str | None = None
    budget: float = 5.0
    max_turns: int = 50

    @model_validator(mode="after")
    def _require_target_or_name(self) -> "CreateSession":
        if self.target is None and self.target_name is None:
            raise ValueError(
                "Either 'target' or 'target_name' must be provided"
            )
        return self


class MessageBody(BaseModel):
    text: str


class BudgetBody(BaseModel):
    budget: float | None = None
    max_turns: int | None = None


class SudoBody(BaseModel):
    password: str


class UpdateConfigBody(BaseModel):
    backend: str | None = None
    model: str | None = None
    api_base: str | None = None
    profile: str | None = None
    budget: float | None = None
    max_turns: int | None = None


def _manager(request: Request):
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is None:
        raise HTTPException(500, detail="session_manager not configured")
    return mgr


def _clean_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@router.get("/api/sessions")
def list_sessions(request: Request) -> dict:
    return {"sessions": _manager(request).list_sessions()}


@router.post("/api/sessions")
async def create_session(request: Request, body: CreateSession) -> dict:
    # Resolve the effective target address string.
    if body.target_name is not None:
        # New path: resolve by Target name (with optional address override).
        from ...session_start import resolve_target
        override = body.address
        if override is None and body.target is not None and body.target != body.target_name:
            override = body.target
        try:
            resolved = resolve_target(body.target_name, override_address=override)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"could not resolve target {body.target_name!r}: {exc}",
            )
        effective_target = resolved.primary_address.value
    else:
        # Legacy path: raw address / binary path.
        from ...session_start import resolve_target
        try:
            resolved = resolve_target(body.target or "")
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"could not resolve target {body.target!r}: {exc}",
            )
        effective_target = resolved.primary_address.value

    try:
        return await _manager(request).create_session(
            target=effective_target,
            profile_key=body.profile,
            backend_name=body.backend,
            model=_clean_optional_str(body.model),
            api_base=_clean_optional_str(body.api_base),
            budget=body.budget,
            max_turns=body.max_turns,
            target_obj=resolved,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/api/sessions/{session_id}/messages", status_code=204)
async def send_message(request: Request, session_id: str, body: MessageBody) -> Response:
    mgr = _manager(request)
    try:
        gs = mgr.get_active(session_id)
    except KeyError:
        row = next((r for r in mgr.list_sessions() if r["id"] == session_id), None)
        if row is None:
            raise HTTPException(404)
        try:
            snap = load_snapshot(row["target"], session_id)
        except SessionNotFoundError:
            raise HTTPException(404)
        if snap.state != "active" or is_session_alive(snap):
            raise HTTPException(404)
        try:
            await mgr.resume_session(
                snapshot_id=session_id,
                target=row["target"],
                backend_name=None,
                model=None,
                api_base=None,
            )
            gs = mgr.get_active(session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"could not resume stale active session: {exc}",
            )
    await gs.send_message(body.text)
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/pending-messages")
async def queue_pending_message(request: Request, session_id: str, body: MessageBody) -> dict:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    return await gs.queue_message(body.text)


@router.delete("/api/sessions/{session_id}/pending-messages/{message_id}", status_code=204)
async def delete_pending_message(request: Request, session_id: str, message_id: str) -> Response:
    try:
        gs = _manager(request).get_active(session_id)
    except KeyError:
        raise HTTPException(404)
    deleted = await gs.delete_pending_message(message_id)
    if not deleted:
        raise HTTPException(404)
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
    mgr = _manager(request)
    try:
        gs = mgr.get_active(session_id)
    except KeyError:
        pass
    else:
        # Cancel any in-flight send_message task first so a long-running tool
        # call unwinds promptly. Then mark stopped, release resources, and
        # clear manager.active so list_sessions() stops overriding state to
        # "active" (see session_manager.list_sessions, which clobbers on-disk
        # state for whatever GUISession sits in mgr.active).
        gs.cancel()
        gs.stop()
        gs.close()
        mgr.active = None
        return Response(status_code=204)

    # Fall back to a disk-only snapshot mutation. Covers stale "active"
    # snapshots whose original process is gone — e.g. orphans left behind
    # by an earlier crash or by tests that wrote to the wrong targets dir.
    # Mirrors the same fallback in mark_done().
    row = next((r for r in mgr.list_sessions() if r["id"] == session_id), None)
    if row is None:
        raise HTTPException(404)
    try:
        snap = load_snapshot(row["target"], session_id)
    except SessionNotFoundError:
        raise HTTPException(404)
    if snap.state != "completed":
        snap.state = "stopped"
        snap.stopped_at = snap.stopped_at or snap.last_active_at
        snap.pid = None
        save_snapshot(snap)
    return Response(status_code=204)


@router.post("/api/sessions/{session_id}/done", status_code=204)
async def mark_done(request: Request, session_id: str) -> Response:
    mgr = _manager(request)
    try:
        gs = mgr.get_active(session_id)
        # See stop_session for why we cancel/close/clear-active here.
        gs.cancel()
        gs.mark_completed()
        gs.close()
        mgr.active = None
        return Response(status_code=204)
    except KeyError:
        pass

    # Fall back to a disk-only snapshot mutation. This covers historical
    # sessions and stale-'active' snapshots whose original process is gone.
    row = next((r for r in mgr.list_sessions() if r["id"] == session_id), None)
    if row is None:
        raise HTTPException(404)
    try:
        snap = load_snapshot(row["target"], session_id)
    except SessionNotFoundError:
        raise HTTPException(404)
    snap.state = "completed"
    snap.stopped_at = snap.stopped_at or snap.last_active_at
    snap.pid = None
    save_snapshot(snap)
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
    mgr = _manager(request)
    try:
        gs = mgr.get_active(session_id)
    except KeyError:
        # Fall back to writing the process-wide sudo store directly. The
        # SudoModal renders from the cached `useSessions` row state, which
        # can lag mgr.active by up to 5 s (post-stop/done/create-new) and
        # can also report "active" for orphan snapshots after a backend
        # restart — both windows produce Save clicks against a session_id
        # that's no longer mgr.active. The password is process-global
        # (tools/_common._sudo_password), so writing it here works for
        # whatever GUISession runs next regardless of which row the user
        # clicked. Still 404 when the session_id is unknown entirely, so
        # typos can't silently set the global.
        if not any(r["id"] == session_id for r in mgr.list_sessions()):
            raise HTTPException(404)
        from ...tools._common import set_sudo_password
        set_sudo_password(body.password)
        os.environ["REVERSER_SUDO_PASSWORD"] = body.password
        return Response(status_code=204)
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

    Filters to {turn, text, thinking, tool_call, tool_result, dispatch}.
    Caps at 5000 events (oldest dropped). `turn` and `text` are essential
    for the read-only view: `turn` lets the frontend bucket per-turn
    events into the right Turn, and `text` carries the LLM's assistant
    response (without it the main pane shows only tool chips).
    """
    try:
        snap = load_snapshot(target, session_id)
    except SessionNotFoundError:
        raise HTTPException(404, detail=f"unknown session: {session_id!r}")

    log_path = snap.log_path
    if not log_path or not os.path.isfile(log_path):
        raise HTTPException(404, detail=f"session log file not found: {log_path!r}")

    user_events_by_turn: dict[int, list[dict]] = {}
    for conv_entry in snap.conversation:
        user_text = (conv_entry.user or "").strip()
        if not user_text:
            continue
        turn = conv_entry.turn or 0
        user_events_by_turn.setdefault(turn, []).append({
            "kind": "user",
            "turn": turn,
            "content": conv_entry.user,
            "ts": conv_entry.timestamp,
        })

    raw = load_session_log(log_path)
    _ALLOWED = {"turn", "text", "thinking", "tool_call", "tool_result", "dispatch"}

    out: list[dict] = []
    emitted_user_turns: set[int] = set()
    for entry in raw:
        kind = entry.get("type")
        if kind not in _ALLOWED:
            continue
        ts = entry.get("ts")
        if kind == "turn":
            turn = entry.get("turn", 0)
            if turn not in emitted_user_turns:
                out.extend(user_events_by_turn.get(turn, []))
                emitted_user_turns.add(turn)
            out.append({"kind": "turn", "turn": turn, "ts": ts})
        elif kind == "text":
            out.append({"kind": "text", "content": entry.get("text", ""), "ts": ts})
        elif kind == "thinking":
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

    for turn in sorted(user_events_by_turn):
        if turn not in emitted_user_turns:
            out.extend(user_events_by_turn[turn])

    truncated = len(out) > 5000
    if truncated:
        out = out[-5000:]

    return {"id": session_id, "events": out, "truncated": truncated}


@router.patch("/api/sessions/{session_id}/config", status_code=204)
def update_session_config(
    request: Request, session_id: str, target: str, body: UpdateConfigBody,
) -> Response:
    """Edit a stopped engagement's config. Saves to the snapshot so the next
    Resume picks up the new values. Refuses when the engagement is running
    or terminal."""
    from ...profiles import get_profile as _get_profile

    mgr = _manager(request)
    cached_gs = None
    if mgr.active is not None and mgr.active.session_id == session_id:
        cached_gs = mgr.active

    if cached_gs is not None:
        # Use the live in-memory snapshot so list_sessions's override-from-active
        # path returns the new values immediately.
        snap = cached_gs._agent._snapshot
    else:
        try:
            snap = load_snapshot(target, session_id)
        except SessionNotFoundError:
            raise HTTPException(404)

    if snap.state == "active":
        raise HTTPException(409, detail="engagement is running; stop it first")
    if snap.state in ("completed", "abandoned"):
        raise HTTPException(
            409, detail=f"engagement is {snap.state}; config cannot be changed",
        )

    # `exclude_unset=True` keeps only fields the client actually sent — so
    # `{"model": null}` (clear back to default) is distinguishable from
    # `{}` (don't touch model). Optional fields (model, api_base) accept
    # null; required ones (backend, profile, budget, max_turns) do not.
    fields = body.model_dump(exclude_unset=True)
    if "model" in fields:
        fields["model"] = _clean_optional_str(fields["model"])
    if "api_base" in fields:
        fields["api_base"] = _clean_optional_str(fields["api_base"])

    if "profile" in fields:
        if fields["profile"] is None:
            raise HTTPException(400, detail="profile cannot be null")
        try:
            _get_profile(fields["profile"])
        except KeyError:
            raise HTTPException(
                400, detail=f"unknown profile: {fields['profile']!r}",
            )
    if "backend" in fields:
        if fields["backend"] is None:
            raise HTTPException(400, detail="backend cannot be null")
        if fields["backend"] not in _VALID_BACKENDS:
            raise HTTPException(
                400,
                detail=f"unknown backend: {fields['backend']!r}. "
                       f"Known: {sorted(_VALID_BACKENDS)}",
            )
    if "budget" in fields:
        if fields["budget"] is None or fields["budget"] <= 0:
            raise HTTPException(400, detail="budget must be > 0")
    if "max_turns" in fields:
        if fields["max_turns"] is None or fields["max_turns"] < 1:
            raise HTTPException(400, detail="max_turns must be >= 1")

    # Cross-field validation: if the resulting (backend, model) pairing would
    # leave a requires_model backend without a model, reject now rather than
    # letting resume fail with an obscure error.
    final_backend = fields.get("backend", snap.config.backend)
    final_model = fields.get("model", snap.config.model)
    if final_backend in _BACKENDS_REQUIRING_MODEL and final_model is None:
        raise HTTPException(
            400,
            detail=f"backend {final_backend!r} requires a non-null model",
        )

    # Apply only sent fields. `model` and `api_base` may legitimately be None.
    # Note: when `cached_gs is not None`, `snap` IS `cached_gs._agent._snapshot`,
    # so mutating snap.config also updates what `_serialize()` in session_manager
    # reads via `gs._agent._snapshot.config` — no extra sync needed for these four.
    if "backend" in fields:
        snap.config.backend = fields["backend"]
    if "model" in fields:
        snap.config.model = fields["model"]
    if "api_base" in fields:
        snap.config.api_base = fields["api_base"]
    if "profile" in fields:
        snap.config.profile = fields["profile"]

    # Budget/max_turns need explicit sync to `_agent.{budget,max_turns}` and
    # `_agent.stats.{budget,max_turns}` because `_serialize()` reads those from
    # `gs.stats` (not from the snapshot config). Inline the relevant assignments
    # instead of calling update_budget/update_max_turns (which would each call
    # save_snapshot again, causing a double-write).
    if "budget" in fields:
        snap.config.budget = fields["budget"]
        if cached_gs is not None:
            cached_gs._agent.budget = float(fields["budget"])
            cached_gs._agent.stats.budget = float(fields["budget"])
    if "max_turns" in fields:
        snap.config.max_turns = fields["max_turns"]
        if cached_gs is not None:
            cached_gs._agent.max_turns = int(fields["max_turns"])
            cached_gs._agent.stats.max_turns = int(fields["max_turns"])

    save_snapshot(snap)
    return Response(status_code=204)


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
