"""Crawl/intelligence worker: N concurrent pipeline slots, each claiming one candidate at a time
from any claimable run. Honors pause/stop instantly (no new claims) and lets in-flight work finish."""
from __future__ import annotations

import asyncio
import uuid

from app.brain.strategies import active_config
from app.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.db.models import Mission, Run
from app.events.publisher import emit
from app.events.types import Category, Event
from app.frontier import service as frontier
from app.intelligence.pipeline import PipelineContext, process_candidate
from app.workers.base import BaseWorker

log = get_logger("crawl-worker")


class CrawlWorker(BaseWorker):
    worker_type = "crawler"

    def __init__(self, index: int = 0, concurrency: int | None = None):
        super().__init__(index)
        self.concurrency = concurrency or get_settings().worker_concurrency
        self._slots: dict[int, dict | None] = {}
        self._rr = 0

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._slot(i), name=f"{self.worker_id}-slot{i}") for i in range(self.concurrency)]
        await self.stopping.wait()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _refresh_job(self) -> None:
        active = [j for j in self._slots.values() if j]
        self.current_job = {"active": len(active), "jobs": active[:8]}

    async def _slot(self, slot: int) -> None:
        idle_sleep = 1.0
        while not self.stopping.is_set():
            try:
                claimed = await self._claim()
            except Exception as e:
                log.error("claim failed", extra={"extra": {"error": str(e)}})
                await self.sleep(3)
                continue
            if not claimed:
                await self.sleep(idle_sleep)
                idle_sleep = min(5.0, idle_sleep * 1.5)
                continue
            idle_sleep = 1.0
            run_id, mission_id, cand_id, domain = claimed
            self._slots[slot] = {"domain": domain, "run_id": str(run_id), "stage": "crawl", "started_at": _now()}
            self._refresh_job()
            try:
                await self._process(run_id, mission_id, cand_id)
                self.completed += 1
            except Exception as e:
                self.failed += 1
                log.exception("pipeline error", extra={"extra": {"domain": domain}})
                try:
                    async with session_scope() as s:
                        cand = await s.get(__import__("app.db.models", fromlist=["CandidateDomain"]).CandidateDomain, cand_id)
                        if cand:
                            await frontier.fail_or_retry(s, cand, "worker_exception", str(e)[:200])
                        await emit(s, Event.WORKER_ERROR, f"{self.worker_id} failed on {domain}: {str(e)[:150]}", category=Category.ERRORS, level="error",
                                   run_id=run_id, mission_id=mission_id, worker_id=self.worker_id, domain=domain)
                except Exception:
                    pass
            finally:
                self._slots[slot] = None
                self._refresh_job()

    async def _claim(self):
        async with session_scope() as s:
            runs = await self.claimable_runs(s)
            if not runs:
                return None
            for _ in range(len(runs)):
                run = runs[self._rr % len(runs)]
                self._rr += 1
                cand = await frontier.claim_next(s, run.mission_id, run.id, self.worker_id)
                if cand:
                    return run.id, run.mission_id, cand.id, cand.normalized_domain
            return None

    async def _process(self, run_id: uuid.UUID, mission_id: uuid.UUID, cand_id: uuid.UUID) -> None:
        from app.db.models import CandidateDomain

        async with session_scope() as s:
            run = await s.get(Run, run_id)
            mission = await s.get(Mission, mission_id)
            cand = await s.get(CandidateDomain, cand_id)
            cfg, label = await active_config(s, mission.parsed_objective.get("vertical", "business"))
            ctx = PipelineContext(session=s, run=run, mission=mission, candidate=cand, worker_id=self.worker_id, strategy_config=cfg, strategy_label=label)
            await process_candidate(ctx)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
