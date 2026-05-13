"""WebSocket endpoint /ws/sessions/{session_id}.

Subscribes to EventBus for the given session_id and forwards every frame
as a JSON text message. Auth is via ?token=… in the query string.
"""
import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..auth import is_authorized_query

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}")
async def session_stream(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
):
    config = websocket.app.state.config
    if not is_authorized_query(token, config.token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    bus = websocket.app.state.event_bus
    await websocket.accept()
    try:
        async with bus.subscribe(session_id) as queue:
            while True:
                # Race the consumer-direction recv (for client → server frames
                # like {"type":"pause"}) against the queue (server → client).
                # The first to resolve wins.
                recv_task = asyncio.create_task(websocket.receive_text())
                q_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    {recv_task, q_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if recv_task in done:
                    # We accept but currently ignore client → server frames.
                    # Future: {"type":"pause"|"abort_tool"}.
                    _ = recv_task.result()
                if q_task in done:
                    frame = q_task.result()
                    await websocket.send_json(frame)
    except WebSocketDisconnect:
        return
