from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Run, RunCheckpoint
from app.frontier import service as frontier


async def create_checkpoint(session: AsyncSession, run: Run, reason: str) -> RunCheckpoint:
    c = await frontier.counts(session, mission_id=run.mission_id)
    cp = RunCheckpoint(
        run_id=run.id, reason=reason, frontier_count=c["queued_total"], processing_count=c["PROCESSING"],
        strategy_version=run.strategy_version, cursor_state=dict(run.config.get("cursor_state", {})),
        stats=dict(run.stats or {}),
    )
    session.add(cp)
    await session.flush()
    run.checkpoint_id = cp.id
    return cp


async def latest_checkpoint(session: AsyncSession, run_id: uuid.UUID) -> RunCheckpoint | None:
    from sqlalchemy import desc, select

    return await session.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == run_id).order_by(desc(RunCheckpoint.created_at)).limit(1))
