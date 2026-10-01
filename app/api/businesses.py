from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.exceptions import NotFound
from app.db.models import ActivityEvent, BusinessNode, CandidateDomain
from app.graph import queries

router = APIRouter(prefix="/businesses", tags=["businesses"])


async def _get(session: AsyncSession, business_id: uuid.UUID) -> BusinessNode:
    b = await session.get(BusinessNode, business_id)
    if not b:
        raise NotFound("business not found")
    return b


@router.get("")
async def list_(session: AsyncSession = Depends(get_session), q: str | None = None, country: str | None = None, category_id: uuid.UUID | None = None,
                confidence_min: float | None = None, limit: int = Query(50, le=200), offset: int = 0, order: str = "recent"):
    return await queries.search_businesses(session, q=q, country=country, category_id=category_id, min_conf=confidence_min, limit=limit, offset=offset, order=order)


@router.get("/domain/{domain}")
async def by_domain(domain: str, session: AsyncSession = Depends(get_session)):
    b = await session.scalar(select(BusinessNode).where(BusinessNode.domain == domain.lower()))
    if not b:
        raise NotFound("business not found")
    return await queries.business_detail(session, b)


@router.get("/{business_id}")
async def get(business_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await queries.business_detail(session, await _get(session, business_id))


@router.get("/{business_id}/evidence")
async def evidence(business_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await queries.business_evidence(session, business_id)


@router.get("/{business_id}/history")
async def history(business_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await queries.business_history(session, business_id)


@router.get("/{business_id}/relationships")
async def relationships(business_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await queries.business_relationships(session, await _get(session, business_id))


@router.get("/{business_id}/pages")
async def pages(business_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await queries.business_pages(session, await _get(session, business_id))


@router.get("/{business_id}/activity")
async def activity(business_id: uuid.UUID, session: AsyncSession = Depends(get_session), limit: int = Query(100, le=500)):
    b = await _get(session, business_id)
    domains = {b.domain, b.registrable_domain, *((b.metadata_ or {}).get("aliases", []))}
    rows = (await session.scalars(select(ActivityEvent).where(ActivityEvent.domain.in_(domains)).order_by(desc(ActivityEvent.created_at)).limit(limit))).all()
    return [{"id": e.id, "event_type": e.event_type, "category": e.category, "level": e.level, "message": e.message, "worker_id": e.worker_id,
             "metadata": e.metadata_, "run_id": str(e.run_id) if e.run_id else None, "created_at": e.created_at.isoformat()} for e in rows]


@router.get("/{business_id}/discoveries")
async def discoveries(business_id: uuid.UUID, session: AsyncSession = Depends(get_session), limit: int = Query(100, le=500)):
    """Candidates discovered from this business's outbound links."""
    b = await _get(session, business_id)
    rows = (await session.scalars(select(CandidateDomain).where(CandidateDomain.source_reference == b.registrable_domain,
                                                                 CandidateDomain.source_type.in_(("outbound_link", "directory_link")))
                                  .order_by(desc(CandidateDomain.first_discovered_at)).limit(limit))).all()
    return [{"domain": c.normalized_domain, "status": c.status, "priority": c.priority, "discovered_at": c.first_discovered_at.isoformat(),
             "business_id": (c.metadata_ or {}).get("business_id")} for c in rows]
