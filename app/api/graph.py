from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.exceptions import NotFound
from app.db.models import CategoryNode
from app.graph import queries
from app.graph.neo4j_store import get_neo4j
from app.graph.taxonomy import ROOT_SLUG

router = APIRouter(prefix="/graph", tags=["graph"])


def _created_after(created_after: datetime | None, last_24h: bool) -> datetime | None:
    if last_24h:
        return datetime.now(timezone.utc) - timedelta(hours=24)
    return created_after


@router.get("/root")
async def root(session: AsyncSession = Depends(get_session), country: str | None = None, confidence_min: float | None = None,
               created_after: datetime | None = None, last_24h: bool = False):
    root = await session.scalar(select(CategoryNode).where(CategoryNode.slug == ROOT_SLUG))
    totals = await queries.graph_totals(session)
    kids = await queries.children(session, None, country=country, min_conf=confidence_min, created_after=_created_after(created_after, last_24h))
    return {"root": {"id": str(root.id) if root else None, "name": "Business", "slug": ROOT_SLUG, "business_count": totals["businesses"]}, "children": kids}


@router.get("/categories/{category_id}/children")
async def children(category_id: uuid.UUID, session: AsyncSession = Depends(get_session), country: str | None = None, confidence_min: float | None = None,
                   created_after: datetime | None = None, last_24h: bool = False):
    return await queries.children(session, category_id, country=country, min_conf=confidence_min, created_after=_created_after(created_after, last_24h))


@router.get("/categories/{category_id}/businesses")
async def category_businesses(category_id: uuid.UUID, session: AsyncSession = Depends(get_session), limit: int = Query(50, le=200), cursor: str | None = None,
                              country: str | None = None, confidence_min: float | None = None, created_after: datetime | None = None, last_24h: bool = False):
    cat = await session.get(CategoryNode, category_id)
    if not cat:
        raise NotFound("category not found")
    return await queries.category_businesses(session, cat, limit=limit, cursor=cursor, country=country, min_conf=confidence_min,
                                             created_after=_created_after(created_after, last_24h))


@router.get("/search")
async def search(q: str = Query(min_length=1), session: AsyncSession = Depends(get_session), limit: int = Query(20, le=50)):
    like = f"%{q.lower()}%"
    from sqlalchemy import func

    cats = (await session.scalars(select(CategoryNode).where(func.lower(CategoryNode.name).like(like), CategoryNode.slug != ROOT_SLUG).limit(limit))).all()
    biz = await queries.search_businesses(session, q=q, country=None, category_id=None, min_conf=None, limit=limit, offset=0)
    return {"categories": [{"id": str(c.id), "name": c.name, "path": c.path, "depth": c.depth} for c in cats], "businesses": biz["items"]}


@router.get("/neighborhood/{domain}")
async def neighborhood(domain: str, limit: int = Query(50, le=200)):
    return await get_neo4j().neighborhood(domain, limit)
