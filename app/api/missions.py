from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.core.database import get_session
from app.llm.router import get_router
from app.missions import service
from app.missions.parser import interpret
from app.missions.schemas import MissionCreate, MissionOut, MissionUpdate
from app.runs.service import create_run

router = APIRouter(prefix="/missions", tags=["missions"])


@router.post("/interpret")
async def interpret_prompt(body: MissionCreate):
    """Preview the mission interpretation without persisting anything."""
    plan, provider = await interpret(body.prompt)
    return {"interpretation": plan.model_dump(), "provider": provider, "model": get_router().model_name}


@router.post("", response_model=MissionOut, status_code=201, dependencies=[Depends(require_api_key)])
async def create(body: MissionCreate, session: AsyncSession = Depends(get_session)):
    mission = await service.create_mission(session, body.prompt)
    if body.start:
        await create_run(session, mission.id)
    return await service.to_out(session, mission)


@router.get("", response_model=list[MissionOut])
async def list_(session: AsyncSession = Depends(get_session)):
    return [await service.to_out(session, m) for m in await service.list_missions(session)]


@router.get("/{mission_id}", response_model=MissionOut)
async def get(mission_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await service.to_out(session, await service.get_mission(session, mission_id))


@router.patch("/{mission_id}", response_model=MissionOut, dependencies=[Depends(require_api_key)])
async def patch(mission_id: uuid.UUID, body: MissionUpdate, session: AsyncSession = Depends(get_session)):
    m = await service.get_mission(session, mission_id)
    if body.name:
        m.name = body.name
    if body.status:
        m.status = body.status
    return await service.to_out(session, m)


@router.post("/{mission_id}/runs", status_code=201, dependencies=[Depends(require_api_key)])
async def start_run(mission_id: uuid.UUID, session: AsyncSession = Depends(get_session), parent_run_id: uuid.UUID | None = None):
    run = await create_run(session, mission_id, parent_run_id=parent_run_id)
    return {"id": str(run.id), "status": run.status, "mission_id": str(run.mission_id)}
