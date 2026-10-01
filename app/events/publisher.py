"""Activity events: persisted to PostgreSQL (audit / replay) and fanned out over Redis pub/sub
(live control room). Every meaningful action in the system goes through `emit`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import EVENTS_CHANNEL, get_redis
from app.db.models import ActivityEvent
from app.events.types import Category, Event

log = get_logger("events")


def _json_default(o: Any):
    if isinstance(o, (uuid.UUID, datetime)):
        return str(o)
    raise TypeError


def dumps(obj: Any) -> str:
    return orjson.dumps(obj, default=_json_default).decode()


async def emit(
    session: AsyncSession | None,
    event: Event | str,
    message: str,
    *,
    category: Category | str = Category.SYSTEM,
    run_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    worker_id: str | None = None,
    domain: str | None = None,
    level: str = "info",
    payload: dict | None = None,
    persist: bool = True,
) -> dict:
    ts = datetime.now(timezone.utc)
    envelope = {
        "event": str(event),
        "timestamp": ts.isoformat(),
        "run_id": str(run_id) if run_id else None,
        "mission_id": str(mission_id) if mission_id else None,
        "worker_id": worker_id,
        "category": str(category),
        "level": level,
        "domain": domain,
        "message": message,
        "payload": payload or {},
    }
    if persist and session is not None:
        session.add(
            ActivityEvent(
                run_id=uuid.UUID(str(run_id)) if run_id else None,
                mission_id=uuid.UUID(str(mission_id)) if mission_id else None,
                worker_id=worker_id,
                category=str(category),
                event_type=str(event),
                level=level,
                domain=domain,
                message=message,
                metadata_=payload or {},
                created_at=ts,
            )
        )
    try:
        await get_redis().publish(EVENTS_CHANNEL, dumps(envelope))
    except Exception as e:  # never let observability take down the pipeline
        log.warning("redis publish failed", extra={"extra": {"error": str(e)}})
    return envelope


async def broadcast(event: Event | str, payload: dict, run_id: uuid.UUID | str | None = None, **kw) -> None:
    """Non-persisted, high-frequency events (metrics ticks, stage changes)."""
    await emit(None, event, kw.pop("message", str(event)), run_id=run_id, payload=payload, persist=False, **kw)
