"""Redis pub/sub -> WebSocket fan-out. One subscriber task per API process; per-socket
filters by run_id. On reconnect the client reloads the REST snapshot then re-attaches."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import orjson
from fastapi import WebSocket

from app.core.logging import get_logger
from app.core.redis import EVENTS_CHANNEL, get_redis

log = get_logger("ws")


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, str | None] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump(), name="ws-pump")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def connect(self, ws: WebSocket, run_id: str | None) -> None:
        await ws.accept()
        async with self._lock:
            self._clients[ws] = run_id

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)

    async def _pump(self) -> None:
        while True:
            pubsub = get_redis().pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.subscribe(EVENTS_CHANNEL)
                while True:
                    # Poll timeout must stay below the client's socket_timeout (5s default in redis-py 8),
                    # otherwise an idle channel raises TimeoutError instead of returning None.
                    msg = await pubsub.get_message(timeout=1.0)
                    if msg is None or msg.get("type") != "message":
                        continue
                    raw: str = msg["data"]
                    try:
                        env: dict[str, Any] = orjson.loads(raw)
                    except Exception:
                        continue
                    await self._dispatch(raw, env.get("run_id"))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("ws pump error, reconnecting", extra={"extra": {"error": str(e)}})
                await asyncio.sleep(1)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def _dispatch(self, raw: str, run_id: str | None) -> None:
        async with self._lock:
            targets = [ws for ws, filt in self._clients.items() if filt is None or filt == run_id or run_id is None]
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


hub = WebSocketHub()
