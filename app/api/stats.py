from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.metrics import service as m
from app.stats.service import overview

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview")
async def get_overview(session: AsyncSession = Depends(get_session), range: str = Query("24h"), mission_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None):
    return await overview(session, range, mission_id, run_id)


@router.get("/series")
async def get_series(session: AsyncSession = Depends(get_session), metric: str = "business_created", hours: int = Query(24, le=24 * 30),
                     mission_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None):
    scope = f"run:{run_id}" if run_id else f"mission:{mission_id}" if mission_id else "global"
    return {"metric": metric, "scope": scope, "points": await m.series(session, metric, scope, hours)}


@router.get("/changes")
async def changes(session: AsyncSession = Depends(get_session), hours: int = Query(24, le=24 * 30), run_id: uuid.UUID | None = None):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.db.models import ChangeEvent

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(ChangeEvent.event_type, func.count()).where(ChangeEvent.created_at >= since).group_by(ChangeEvent.event_type)
    if run_id:
        stmt = stmt.where(ChangeEvent.run_id == run_id)
    rows = (await session.execute(stmt)).all()
    return {"hours": hours, "counts": {t: int(n) for t, n in rows}}
