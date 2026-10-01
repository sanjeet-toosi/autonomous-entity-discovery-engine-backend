"""Reflection worker: periodically turns experiences into proposed lessons + strategy candidates."""
from __future__ import annotations

from app.brain.reflection import reflect_mission
from app.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.db.models import Mission
from app.workers.base import BaseWorker

log = get_logger("reflection-worker")


class ReflectionWorker(BaseWorker):
    worker_type = "reflection"

    async def run(self) -> None:
        s = get_settings()
        while not self.stopping.is_set():
            try:
                async with session_scope() as sess:
                    runs = await self.claimable_runs(sess)
                    for run in runs:
                        mission = await sess.get(Mission, run.mission_id)
                        self.current_job = {"run_id": str(run.id), "stage": "reflect"}
                        out = await reflect_mission(sess, mission.id, mission.parsed_objective.get("vertical", "business"), run_id=run.id,
                                                    min_experiences=s.reflection_min_experiences)
                        log.info("reflection", extra={"extra": {"run_id": str(run.id), **{k: v for k, v in out.items() if k != 'lessons'}}})
                        self.completed += 1
                self.current_job = None
            except Exception:
                log.exception("reflection failed")
                self.failed += 1
            await self.sleep(s.reflection_interval_seconds)
