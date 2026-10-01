from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.events.websocket import hub

router = APIRouter()


@router.websocket("/ws/runs/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
    await hub.connect(ws, run_id)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignored
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)


@router.websocket("/ws/global")
async def ws_global(ws: WebSocket):
    await hub.connect(ws, None)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)
