from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.db.models import ActivityEvent, ChangeEvent

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("")
async def list_events(session: AsyncSession = Depends(get_session), run_id: uuid.UUID | None = None, category: str | None = None,
                      domain: str | None = None, before_id: int | None = None, limit: int = Query(100, le=500)):
    stmt = select(ActivityEvent).order_by(desc(ActivityEvent.id)).limit(limit)
    if run_id:
        stmt = stmt.where(ActivityEvent.run_id == run_id)
    if category and category != "all":
        stmt = stmt.where(ActivityEvent.category == category)
    if domain:
        stmt = stmt.where(ActivityEvent.domain == domain)
    if before_id:
        stmt = stmt.where(ActivityEvent.id < before_id)
    rows = (await session.scalars(stmt)).all()
    return [{"id": e.id, "event": e.event_type, "category": e.category, "level": e.level, "domain": e.domain, "message": e.message, "worker_id": e.worker_id,
             "run_id": str(e.run_id) if e.run_id else None, "payload": e.metadata_, "timestamp": e.created_at.isoformat()} for e in rows]


@router.get("/changes")
async def list_changes(session: AsyncSession = Depends(get_session), run_id: uuid.UUID | None = None, event_type: str | None = None, limit: int = Query(100, le=500)):
    stmt = select(ChangeEvent).order_by(desc(ChangeEvent.created_at)).limit(limit)
    if run_id:
        stmt = stmt.where(ChangeEvent.run_id == run_id)
    if event_type:
        stmt = stmt.where(ChangeEvent.event_type == event_type)
    rows = (await session.scalars(stmt)).all()
    return [{"id": str(c.id), "event_type": c.event_type, "entity_type": c.entity_type, "entity_id": str(c.entity_id), "before": c.before, "after": c.after,
             "run_id": str(c.run_id) if c.run_id else None, "created_at": c.created_at.isoformat()} for c in rows]
