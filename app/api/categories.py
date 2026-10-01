from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.exceptions import NotFound
from app.db.models import CategoryNode
from app.graph import queries
from app.graph.taxonomy import ROOT_SLUG

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("")
async def list_(session: AsyncSession = Depends(get_session), q: str | None = None, limit: int = Query(200, le=1000)):
    stmt = select(CategoryNode).where(CategoryNode.slug != ROOT_SLUG).order_by(CategoryNode.path).limit(limit)
    if q:
        from sqlalchemy import func

        stmt = stmt.where(func.lower(CategoryNode.name).like(f"%{q.lower()}%"))
    rows = (await session.scalars(stmt)).all()
    return [{"id": str(c.id), "slug": c.slug, "name": c.name, "path": c.path, "depth": c.depth, "parent_id": str(c.parent_id) if c.parent_id else None,
             "business_count": c.business_count, "status": c.status, "created_at": c.created_at.isoformat()} for c in rows]


@router.get("/{category_id}")
async def get(category_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    cat = await session.get(CategoryNode, category_id)
    if not cat:
        raise NotFound("category not found")
    detail = await queries.category_detail(session, cat)
    detail["subcategories"] = await queries.children(session, cat.id)
    detail["recent"] = (await queries.category_businesses(session, cat, limit=10, cursor=None, country=None, min_conf=None, created_after=None))["items"]
    return detail
