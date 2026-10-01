"""Operator switches for discovery sources: enable/disable, store API keys, test connectivity."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.core.database import get_session
from app.discovery.sources import get_source
from app.integrations import service

router = APIRouter(prefix="/integrations", tags=["integrations"])


class IntegrationUpdate(BaseModel):
    enabled: bool | None = None
    api_key: str | None = None
    clear_key: bool = False
    config: dict | None = None


@router.get("")
async def list_integrations(session: AsyncSession = Depends(get_session)):
    eff = await service.effective(session)
    return [service.public_view(e) for e in eff.values()]


@router.put("/{key}", dependencies=[Depends(require_api_key)])
async def update_integration(key: str, body: IntegrationUpdate, session: AsyncSession = Depends(get_session)):
    try:
        e = await service.update(session, key, enabled=body.enabled, api_key=body.api_key, clear_key=body.clear_key, config=body.config)
    except KeyError:
        raise HTTPException(404, "unknown integration")
    return service.public_view(e)


@router.post("/{key}/test", dependencies=[Depends(require_api_key)])
async def test_integration(key: str, session: AsyncSession = Depends(get_session)):
    eff = await service.effective(session)
    if key not in eff:
        raise HTTPException(404, "unknown integration")
    e = eff[key]
    src = get_source(key)
    try:
        result = await src.test(e)
    except Exception as ex:  # surface the failure to the operator rather than 500
        result = {"ok": False, "message": f"{type(ex).__name__}: {str(ex)[:200]}"}
    result["tested_at"] = datetime.now(timezone.utc).isoformat()
    await service.record_status(session, key, {"last_test": result})
    return result
