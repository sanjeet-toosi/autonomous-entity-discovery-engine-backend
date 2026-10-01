from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFound
from app.db.models import Mission, Run
from app.events.publisher import emit
from app.events.types import Category, Event
from app.missions.parser import interpret
from app.missions.schemas import MissionOut

ACTIVE_RUN_STATES = ("STARTING", "RUNNING", "PAUSING", "PAUSED", "RESUMING", "STOPPING")


async def create_mission(session: AsyncSession, prompt: str) -> Mission:
    plan, provider = await interpret(prompt)
    parsed = plan.model_dump()
    parsed["planner_provider"] = provider
    mission = Mission(name=plan.summary[:200] if plan.summary else prompt[:200], raw_prompt=prompt, parsed_objective=parsed)
    session.add(mission)
    await session.flush()
    await emit(
        session, Event.MISSION_INTERPRETED, f"Interpreted mission: {plan.summary}", category=Category.SYSTEM,
        mission_id=mission.id, payload={"interpretation": parsed},
    )
    return mission


async def get_mission(session: AsyncSession, mission_id: uuid.UUID) -> Mission:
    m = await session.get(Mission, mission_id)
    if not m:
        raise NotFound("mission not found")
    return m


async def list_missions(session: AsyncSession) -> list[Mission]:
    return list((await session.scalars(select(Mission).order_by(desc(Mission.created_at)))).all())


async def active_run(session: AsyncSession, mission_id: uuid.UUID) -> Run | None:
    return await session.scalar(
        select(Run).where(Run.mission_id == mission_id, Run.status.in_(ACTIVE_RUN_STATES)).order_by(desc(Run.created_at)).limit(1)
    )


async def to_out(session: AsyncSession, m: Mission) -> MissionOut:
    run = await active_run(session, m.id)
    return MissionOut(
        id=m.id, name=m.name, prompt=m.raw_prompt, status=m.status, interpretation=m.parsed_objective,
        created_at=m.created_at, updated_at=m.updated_at, active_run_id=run.id if run else None,
        active_run_status=run.status if run else None,
    )
