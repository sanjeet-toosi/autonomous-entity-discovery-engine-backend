"""Run control: start / pause / resume / stop requests. The orchestrator finishes the
multi-step transitions (PAUSING -> PAUSED etc.) once workers have drained."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFound
from app.core.redis import get_redis, run_control_key
from app.db.models import Run
from app.events.publisher import emit
from app.events.types import Category, Event
from app.missions.service import active_run, get_mission
from app.runs import state_machine as sm
from app.runs.checkpoint import latest_checkpoint


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await session.get(Run, run_id)
    if not run:
        raise NotFound("run not found")
    return run


async def list_runs(session: AsyncSession, mission_id: uuid.UUID | None = None, limit: int = 50) -> list[Run]:
    q = select(Run).order_by(desc(Run.created_at)).limit(limit)
    if mission_id:
        q = q.where(Run.mission_id == mission_id)
    return list((await session.scalars(q)).all())


async def publish_control(run: Run) -> None:
    await get_redis().set(run_control_key(str(run.id)), run.status, ex=86400)


async def transition(session: AsyncSession, run: Run, target: str, message: str | None = None, **payload) -> Run:
    sm.assert_transition(run.status, target)
    before = run.status
    now = datetime.now(timezone.utc)
    run.status = target
    if target == sm.RUNNING:
        run.last_resumed_at = now
        run.started_at = run.started_at or now
    if before == sm.RUNNING and run.last_resumed_at:
        run.active_seconds += (now - run.last_resumed_at).total_seconds()
        run.last_resumed_at = None
    if target in sm.TERMINAL:
        run.ended_at = now
    await session.flush()
    await publish_control(run)
    await emit(
        session, Event.RUN_STATUS_CHANGED, message or f"Run {before} → {target}", category=Category.SYSTEM,
        run_id=run.id, mission_id=run.mission_id, payload={"from": before, "to": target, **payload},
    )
    return run


async def create_run(session: AsyncSession, mission_id: uuid.UUID, config: dict | None = None, parent_run_id: uuid.UUID | None = None) -> Run:
    mission = await get_mission(session, mission_id)
    if mission.status != "ACTIVE":
        raise AppError("mission is archived")
    if await active_run(session, mission_id):
        raise AppError("mission already has an active run", 409)
    cfg = {"seeded": False, "cursor_state": {}, **(config or {})}
    if parent_run_id:
        parent = await get_run(session, parent_run_id)
        cp = await latest_checkpoint(session, parent.id)
        if cp:
            cfg["cursor_state"] = dict(cp.cursor_state)
            cfg["seeded"] = True  # frontier is mission-scoped, so it is already there
    run = Run(mission_id=mission_id, status=sm.DRAFT, config=cfg, parent_run_id=parent_run_id, stats={})
    session.add(run)
    await session.flush()
    await transition(session, run, sm.STARTING, "Run created, starting")
    return run


async def request_pause(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await get_run(session, run_id)
    return await transition(session, run, sm.PAUSING, "Pause requested — draining in-flight work")


async def request_resume(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await get_run(session, run_id)
    return await transition(session, run, sm.RESUMING, "Resume requested — restoring checkpoint")


async def request_stop(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await get_run(session, run_id)
    if run.status in sm.TERMINAL:
        raise AppError(f"run already {run.status}", 409)
    return await transition(session, run, sm.STOPPING, "Stop requested — cancelling work, preserving state")


def runtime_seconds(run: Run) -> float:
    total = run.active_seconds
    if run.status == sm.RUNNING and run.last_resumed_at:
        total += (datetime.now(timezone.utc) - run.last_resumed_at).total_seconds()
    return total
