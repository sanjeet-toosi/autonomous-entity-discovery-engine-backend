"""Episodic memory: one record per investigated domain."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrainExperience
from app.metrics import service as metrics


async def record(session: AsyncSession, *, mission_id, run_id, domain: str, vertical: str | None, source_type: str | None,
                 source_reference: str | None, strategy_version: str | None, actions: list[str], result: dict, cost: dict, duration_ms: int) -> BrainExperience:
    e = BrainExperience(
        mission_id=uuid.UUID(str(mission_id)) if mission_id else None, run_id=uuid.UUID(str(run_id)) if run_id else None, domain=domain,
        vertical=vertical, source_type=source_type, source_reference=source_reference, strategy_version=strategy_version,
        actions=actions, result=result, cost=cost, duration_ms=duration_ms,
    )
    session.add(e)
    await metrics.incr(session, metrics.EXPERIENCES, run_id=run_id, mission_id=mission_id)
    return e
