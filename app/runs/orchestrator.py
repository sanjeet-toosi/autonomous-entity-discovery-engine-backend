"""Run Orchestrator: a single background loop (in the API process) that completes multi-step
transitions once workers have drained, expires leases, marks stale workers and broadcasts
metrics snapshots. Idempotent on every tick so restarts are safe."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.brain.strategies import ensure_seed_memories, ensure_strategy
from app.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.db.models import Mission, Run, WorkerHeartbeat
from app.events.publisher import broadcast
from app.events.types import Category, Event
from app.frontier import service as frontier
from app.runs import state_machine as sm
from app.runs.checkpoint import create_checkpoint
from app.runs.service import publish_control, transition
from app.stats.service import run_snapshot

log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._last_metrics = datetime.min.replace(tzinfo=timezone.utc)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="orchestrator")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        s = get_settings()
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("orchestrator tick failed")
            await asyncio.sleep(s.orchestrator_tick_seconds)

    async def tick(self) -> None:
        s = get_settings()
        async with session_scope() as session:
            runs = list((await session.scalars(select(Run).where(Run.status.in_(list(sm.ACTIVE))))).all())
            for run in runs:
                await publish_control(run)  # keep Redis control flag fresh for workers
                if run.status == sm.STARTING:
                    mission = await session.get(Mission, run.mission_id)
                    await ensure_seed_memories(session)
                    _, v = await ensure_strategy(session, mission.parsed_objective.get("vertical", "business"))
                    run.strategy_version = v.label
                    await transition(session, run, sm.RUNNING, f"Run started with strategy {v.label}", strategy=v.label)
                elif run.status == sm.RESUMING:
                    await transition(session, run, sm.RUNNING, "Resumed from checkpoint")
                elif run.status == sm.PAUSING:
                    inflight = await frontier.processing_count(session, run.id)
                    if inflight == 0:
                        cp = await create_checkpoint(session, run, "pause")
                        await transition(session, run, sm.PAUSED, f"Paused — checkpoint saved ({cp.frontier_count:,} candidates in frontier)",
                                         checkpoint_id=str(cp.id))
                elif run.status == sm.STOPPING:
                    inflight = await frontier.processing_count(session, run.id)
                    grace_over = run.updated_at and datetime.now(timezone.utc) - run.updated_at > timedelta(seconds=s.candidate_lease_seconds)
                    if inflight == 0 or grace_over:
                        released = await frontier.release_run_processing(session, run.id)
                        cp = await create_checkpoint(session, run, "stop")
                        await transition(session, run, sm.STOPPED, f"Stopped — graph, frontier and brain preserved ({released} in-flight candidates returned to queue)",
                                         checkpoint_id=str(cp.id))

            released = await frontier.release_expired_leases(session)
            if released:
                log.info("released expired leases", extra={"extra": {"count": released}})

            stale = datetime.now(timezone.utc) - timedelta(seconds=s.worker_stale_seconds)
            await session.execute(update(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat < stale, WorkerHeartbeat.status == "HEALTHY").values(status="UNHEALTHY"))
            await session.execute(update(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat >= stale, WorkerHeartbeat.status == "UNHEALTHY").values(status="HEALTHY"))
            # forget workers that have been gone for a long time so the fleet view stays truthful
            gone = datetime.now(timezone.utc) - timedelta(minutes=10)
            await session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat < gone, WorkerHeartbeat.status != "HEALTHY"))

            now = datetime.now(timezone.utc)
            if now - self._last_metrics >= timedelta(seconds=s.metrics_broadcast_seconds):
                self._last_metrics = now
                for run in runs:
                    if run.status in (sm.RUNNING, sm.PAUSING, sm.STOPPING):
                        snap = await run_snapshot(session, run)
                        await broadcast(Event.METRICS_UPDATED, snap, run_id=run.id, category=Category.SYSTEM)


orchestrator = Orchestrator()
