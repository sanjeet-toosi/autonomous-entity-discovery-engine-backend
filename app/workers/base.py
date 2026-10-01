"""Worker runtime: heartbeats, run-control awareness, graceful shutdown."""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.core.redis import get_redis, run_control_key
from app.db.models import Run, WorkerHeartbeat
from app.events.publisher import emit
from app.events.types import Category, Event
from app.runs import state_machine as sm

log = get_logger("worker")


class BaseWorker:
    worker_type = "generic"

    def __init__(self, index: int = 0, worker_id: str | None = None):
        self.worker_id = worker_id or f"{self.worker_type}-{index}-{os.getpid()}"
        self.stopping = asyncio.Event()
        self.current_job: dict | None = None
        self.completed = 0
        self.failed = 0
        self._hb_task: asyncio.Task | None = None

    # ---- lifecycle ------------------------------------------------------------------
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stopping.set)
            except (NotImplementedError, RuntimeError):
                pass
        await self.heartbeat()
        async with session_scope() as s:
            await emit(s, Event.WORKER_STARTED, f"Worker {self.worker_id} started", category=Category.WORKERS, worker_id=self.worker_id,
                       payload={"worker_type": self.worker_type})
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        try:
            await self.run()
        finally:
            if self._hb_task:
                self._hb_task.cancel()
            await self._mark_stopped()

    async def run(self) -> None:  # pragma: no cover - subclasses
        raise NotImplementedError

    async def _mark_stopped(self) -> None:
        try:
            async with session_scope() as s:
                hb = await s.get(WorkerHeartbeat, self.worker_id)
                if hb:
                    hb.status = "STOPPED"
                    hb.current_job = None
                    hb.last_heartbeat = datetime.now(timezone.utc)
                await emit(s, Event.WORKER_STOPPED, f"Worker {self.worker_id} stopped", category=Category.WORKERS, worker_id=self.worker_id,
                           payload={"completed": self.completed, "failed": self.failed})
        except Exception as e:
            log.warning("could not mark worker stopped", extra={"extra": {"error": str(e)}})

    # ---- heartbeat --------------------------------------------------------------------
    async def heartbeat(self) -> None:
        now = datetime.now(timezone.utc)
        async with session_scope() as s:
            stmt = insert(WorkerHeartbeat).values(
                worker_id=self.worker_id, worker_type=self.worker_type, status="HEALTHY", hostname=socket.gethostname(), pid=os.getpid(),
                current_job=self.current_job, jobs_completed=self.completed, jobs_failed=self.failed, last_heartbeat=now, started_at=now,
            ).on_conflict_do_update(index_elements=[WorkerHeartbeat.worker_id], set_={
                "status": "HEALTHY", "current_job": self.current_job, "jobs_completed": self.completed, "jobs_failed": self.failed, "last_heartbeat": now,
            })
            await s.execute(stmt)

    async def _heartbeat_loop(self) -> None:
        interval = get_settings().worker_heartbeat_seconds
        while not self.stopping.is_set():
            try:
                await self.heartbeat()
            except Exception as e:
                log.warning("heartbeat failed", extra={"extra": {"error": str(e)}})
            await asyncio.sleep(interval)

    # ---- run control ------------------------------------------------------------------
    async def claimable_runs(self, session: AsyncSession) -> list[Run]:
        """Runs whose control state allows scheduling new work (Redis flag, DB fallback)."""
        runs = list((await session.scalars(select(Run).where(Run.status.in_(list(sm.ACTIVE))))).all())
        out = []
        redis = get_redis()
        for r in runs:
            try:
                ctl = await redis.get(run_control_key(str(r.id)))
            except Exception:
                ctl = None
            if (ctl or r.status) in sm.CLAIMABLE:
                out.append(r)
        return out

    async def sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass


def as_uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
