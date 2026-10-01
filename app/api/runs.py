from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.core.database import get_session
from app.db.models import ActivityEvent, Mission, Run, RunCheckpoint
from app.runs import service
from app.stats.service import overview, run_snapshot

router = APIRouter(prefix="/runs", tags=["runs"])


def _run_out(run: Run, mission: Mission | None) -> dict:
    return {
        "id": str(run.id), "mission_id": str(run.mission_id), "mission_prompt": mission.raw_prompt if mission else None,
        "mission_name": mission.name if mission else None, "status": run.status, "strategy_version": run.strategy_version,
        "started_at": run.started_at.isoformat() if run.started_at else None, "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "runtime_seconds": round(service.runtime_seconds(run), 1), "created_at": run.created_at.isoformat(), "config": run.config,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None, "checkpoint_id": str(run.checkpoint_id) if run.checkpoint_id else None,
        "error": run.error,
    }


@router.get("")
async def list_runs(session: AsyncSession = Depends(get_session), mission_id: uuid.UUID | None = None, limit: int = Query(50, le=200)):
    runs = await service.list_runs(session, mission_id, limit)
    out = []
    for r in runs:
        mission = await session.get(Mission, r.mission_id)
        stats = await overview(session, "all", run_id=r.id)
        d = _run_out(r, mission)
        d["stats"] = {"businesses": stats["cards"]["businesses"]["total"], "domains_crawled": stats["metrics"]["domains_crawled"],
                      "candidates": stats["frontier"]["total"], "queued": stats["frontier"]["queued_total"], "errors": stats["metrics"]["crawl_errors"]}
        out.append(d)
    return out


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await service.get_run(session, run_id)
    mission = await session.get(Mission, run.mission_id)
    return {**_run_out(run, mission), "snapshot": await run_snapshot(session, run)}


@router.get("/{run_id}/status")
async def status(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await service.get_run(session, run_id)
    return await run_snapshot(session, run)


@router.get("/{run_id}/checkpoints")
async def checkpoints(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(RunCheckpoint).where(RunCheckpoint.run_id == run_id).order_by(desc(RunCheckpoint.created_at)))).all()
    return [{"id": str(c.id), "reason": c.reason, "frontier_count": c.frontier_count, "processing_count": c.processing_count,
             "strategy_version": c.strategy_version, "cursor_state": c.cursor_state, "created_at": c.created_at.isoformat()} for c in rows]


@router.get("/{run_id}/errors")
async def errors(run_id: uuid.UUID, session: AsyncSession = Depends(get_session), limit: int = Query(100, le=500)):
    rows = (await session.scalars(select(ActivityEvent).where(ActivityEvent.run_id == run_id, ActivityEvent.category == "errors")
                                  .order_by(desc(ActivityEvent.created_at)).limit(limit))).all()
    return [{"id": e.id, "event_type": e.event_type, "level": e.level, "domain": e.domain, "message": e.message, "worker_id": e.worker_id,
             "metadata": e.metadata_, "created_at": e.created_at.isoformat()} for e in rows]


@router.post("/{run_id}/pause", dependencies=[Depends(require_api_key)])
async def pause(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await service.request_pause(session, run_id)
    return {"id": str(run.id), "status": run.status}


@router.post("/{run_id}/resume", dependencies=[Depends(require_api_key)])
async def resume(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await service.request_resume(session, run_id)
    return {"id": str(run.id), "status": run.status}


@router.post("/{run_id}/stop", dependencies=[Depends(require_api_key)])
async def stop(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await service.request_stop(session, run_id)
    return {"id": str(run.id), "status": run.status}


@router.post("/{run_id}/continue", status_code=201, dependencies=[Depends(require_api_key)])
async def continue_from(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Create a new run from a stopped run's checkpoint."""
    old = await service.get_run(session, run_id)
    run = await service.create_run(session, old.mission_id, parent_run_id=old.id)
    return {"id": str(run.id), "status": run.status}
