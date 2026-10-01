"""Read side of the graph: hierarchical, aggregated, paginated. Never returns the whole graph."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, func, select, cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessNode, CategoryNode, ChangeEvent, Evidence, GraphEdge, Page
from app.graph.taxonomy import ROOT_SLUG


async def _rollup_counts(session: AsyncSession, nodes: list[CategoryNode], *, country: str | None, min_conf: float | None,
                         created_after: datetime | None) -> dict[uuid.UUID, int]:
    """Business count under each node including descendants (materialized path prefix)."""
    if not nodes:
        return {}
    filters = []
    if country:
        filters.append(BusinessNode.country_code == country.upper())
    if min_conf is not None:
        filters.append(BusinessNode.confidence >= min_conf)
    if created_after is not None:
        filters.append(BusinessNode.created_at >= created_after)
    out: dict[uuid.UUID, int] = {}
    for n in nodes:
        q = (
            select(func.count(BusinessNode.id))
            .select_from(BusinessNode)
            .join(CategoryNode, CategoryNode.id == BusinessNode.primary_category_id)
            .where((CategoryNode.path == n.path) | CategoryNode.path.like(n.path + "/%"), *filters)
        )
        out[n.id] = int(await session.scalar(q) or 0)
    return out


async def children(session: AsyncSession, parent_id: uuid.UUID | None, *, country: str | None = None, min_conf: float | None = None,
                   created_after: datetime | None = None) -> list[dict]:
    if parent_id is None:
        root = await session.scalar(select(CategoryNode).where(CategoryNode.slug == ROOT_SLUG))
        if root is None:
            return []
        parent_id = root.id
    nodes = list((await session.scalars(
        select(CategoryNode).where(CategoryNode.parent_id == parent_id, CategoryNode.status == "ACTIVE").order_by(CategoryNode.name)
    )).all())
    counts = await _rollup_counts(session, nodes, country=country, min_conf=min_conf, created_after=created_after)
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = await _rollup_counts(session, nodes, country=country, min_conf=min_conf, created_after=since)
    child_counts = dict((await session.execute(
        select(CategoryNode.parent_id, func.count()).where(CategoryNode.parent_id.in_([n.id for n in nodes])).group_by(CategoryNode.parent_id)
    )).all()) if nodes else {}
    return [
        {"id": str(n.id), "slug": n.slug, "name": n.name, "path": n.path, "depth": n.depth, "business_count": counts.get(n.id, 0),
         "added_24h": recent.get(n.id, 0), "subcategory_count": int(child_counts.get(n.id, 0)), "confidence": n.confidence,
         "created_at": n.created_at.isoformat()}
        for n in nodes
    ]


async def category_detail(session: AsyncSession, cat: CategoryNode) -> dict:
    counts = await _rollup_counts(session, [cat], country=None, min_conf=None, created_after=None)
    recent = await _rollup_counts(session, [cat], country=None, min_conf=None, created_after=datetime.now(timezone.utc) - timedelta(hours=24))
    parent = await session.get(CategoryNode, cat.parent_id) if cat.parent_id else None
    sub_count = int(await session.scalar(select(func.count()).where(CategoryNode.parent_id == cat.id)) or 0)
    avg_conf = await session.scalar(
        select(func.avg(BusinessNode.confidence)).join(CategoryNode, CategoryNode.id == BusinessNode.primary_category_id)
        .where((CategoryNode.path == cat.path) | CategoryNode.path.like(cat.path + "/%"))
    )
    top_countries = (await session.execute(
        select(BusinessNode.country_code, func.count()).join(CategoryNode, CategoryNode.id == BusinessNode.primary_category_id)
        .where((CategoryNode.path == cat.path) | CategoryNode.path.like(cat.path + "/%"), BusinessNode.country_code.isnot(None))
        .group_by(BusinessNode.country_code).order_by(func.count().desc()).limit(8)
    )).all()
    history = list((await session.scalars(
        select(ChangeEvent).where(ChangeEvent.entity_type == "category", ChangeEvent.entity_id == cat.id).order_by(ChangeEvent.created_at.desc()).limit(20)
    )).all())
    return {
        "id": str(cat.id), "slug": cat.slug, "name": cat.name, "description": cat.description, "path": cat.path, "depth": cat.depth,
        "status": cat.status, "created_by": cat.created_by, "confidence": cat.confidence, "created_at": cat.created_at.isoformat(),
        "parent": {"id": str(parent.id), "name": parent.name, "slug": parent.slug} if parent else None,
        "business_count": counts.get(cat.id, 0), "added_24h": recent.get(cat.id, 0), "subcategory_count": sub_count,
        "avg_confidence": float(avg_conf or 0), "top_countries": [{"country": c, "count": int(n)} for c, n in top_countries],
        "history": [{"event_type": h.event_type, "before": h.before, "after": h.after, "created_at": h.created_at.isoformat()} for h in history],
    }


async def category_businesses(session: AsyncSession, cat: CategoryNode, *, limit: int, cursor: str | None, country: str | None,
                              min_conf: float | None, created_after: datetime | None) -> dict:
    q = (
        select(BusinessNode).join(CategoryNode, CategoryNode.id == BusinessNode.primary_category_id)
        .where((CategoryNode.path == cat.path) | CategoryNode.path.like(cat.path + "/%"))
        .order_by(BusinessNode.created_at.desc(), BusinessNode.id.desc()).limit(limit + 1)
    )
    if country:
        q = q.where(BusinessNode.country_code == country.upper())
    if min_conf is not None:
        q = q.where(BusinessNode.confidence >= min_conf)
    if created_after is not None:
        q = q.where(BusinessNode.created_at >= created_after)
    if cursor:
        ts, _, bid = cursor.partition("|")
        q = q.where(BusinessNode.created_at < datetime.fromisoformat(ts))
    rows = list((await session.scalars(q)).all())
    nxt = None
    if len(rows) > limit:
        rows = rows[:limit]
        nxt = f"{rows[-1].created_at.isoformat()}|{rows[-1].id}"
    return {"items": [business_summary(b) for b in rows], "next_cursor": nxt}


def business_summary(b: BusinessNode) -> dict:
    return {
        "id": str(b.id), "domain": b.domain, "name": b.name, "country_code": b.country_code, "region": b.region, "city": b.city,
        "confidence": b.confidence, "status": b.status, "business_type": b.business_type,
        "primary_category_id": str(b.primary_category_id) if b.primary_category_id else None,
        "first_seen_at": b.first_seen_at.isoformat(), "last_verified_at": b.last_verified_at.isoformat() if b.last_verified_at else None,
        "created_at": b.created_at.isoformat(),
    }


async def business_detail(session: AsyncSession, b: BusinessNode) -> dict:
    cats: list[CategoryNode] = []
    if b.primary_category_id:
        leaf = await session.get(CategoryNode, b.primary_category_id)
        if leaf:
            slugs = leaf.path.split("/")
            nodes = list((await session.scalars(select(CategoryNode).where(CategoryNode.slug.in_(slugs)))).all())
            by_slug = {n.slug: n for n in nodes}
            cats = [by_slug[s] for s in slugs if s in by_slug and s != ROOT_SLUG]
    return {
        **business_summary(b), "description": b.description, "registrable_domain": b.registrable_domain, "language": b.language,
        "source_type": b.source_type, "source_reference": b.source_reference, "metadata": b.metadata_ or {},
        "discovered_by_run_id": str(b.discovered_by_run_id) if b.discovered_by_run_id else None,
        "last_seen_at": b.last_seen_at.isoformat(), "updated_at": b.updated_at.isoformat(),
        "categories": [{"id": str(c.id), "name": c.name, "slug": c.slug, "depth": c.depth} for c in cats],
    }


async def business_evidence(session: AsyncSession, bid: uuid.UUID) -> list[dict]:
    rows = list((await session.scalars(select(Evidence).where(Evidence.subject_type == "business", Evidence.subject_id == bid).order_by(Evidence.created_at.desc()).limit(100))).all())
    return [{"id": str(e.id), "claim": e.claim, "source_url": e.source_url, "evidence_type": e.evidence_type, "value": e.evidence_value,
             "confidence": e.confidence, "created_at": e.created_at.isoformat()} for e in rows]


async def business_history(session: AsyncSession, bid: uuid.UUID) -> list[dict]:
    rows = list((await session.scalars(select(ChangeEvent).where(ChangeEvent.entity_type == "business", ChangeEvent.entity_id == bid).order_by(ChangeEvent.created_at.desc()).limit(100))).all())
    return [{"id": str(c.id), "event_type": c.event_type, "before": c.before, "after": c.after, "run_id": str(c.run_id) if c.run_id else None,
             "created_at": c.created_at.isoformat()} for c in rows]


async def business_relationships(session: AsyncSession, b: BusinessNode) -> list[dict]:
    q = select(GraphEdge).where(
        ((GraphEdge.source_type == "business") & (GraphEdge.source_id == b.id)) | ((GraphEdge.target_type == "business") & (GraphEdge.target_id == b.id))
    ).order_by(GraphEdge.created_at.desc()).limit(200)
    edges = list((await session.scalars(q)).all())
    biz_ids = {e.source_id for e in edges if e.source_type == "business"} | {e.target_id for e in edges if e.target_type == "business"}
    cat_ids = {e.source_id for e in edges if e.source_type == "category"} | {e.target_id for e in edges if e.target_type == "category"}
    bizs = {x.id: x for x in (await session.scalars(select(BusinessNode).where(BusinessNode.id.in_(biz_ids)))).all()} if biz_ids else {}
    cats = {x.id: x for x in (await session.scalars(select(CategoryNode).where(CategoryNode.id.in_(cat_ids)))).all()} if cat_ids else {}

    def label(t: str, i: uuid.UUID) -> dict:
        if t == "business":
            n = bizs.get(i)
            return {"type": t, "id": str(i), "label": (n.name or n.domain) if n else str(i), "domain": n.domain if n else None}
        n = cats.get(i)
        return {"type": t, "id": str(i), "label": n.name if n else str(i), "slug": n.slug if n else None}

    return [{"id": str(e.id), "relationship_type": e.relationship_type, "confidence": e.confidence, "evidence_count": e.evidence_count,
             "source": label(e.source_type, e.source_id), "target": label(e.target_type, e.target_id), "created_at": e.created_at.isoformat()} for e in edges]


async def business_pages(session: AsyncSession, b: BusinessNode) -> list[dict]:
    rows = list((await session.scalars(select(Page).where(Page.domain == b.registrable_domain).order_by(Page.fetched_at.desc()).limit(50))).all())
    return [{"id": str(p.id), "url": p.url, "final_url": p.final_url, "status_code": p.status_code, "title": p.title, "fetch_ms": p.fetch_ms,
             "fetched_at": p.fetched_at.isoformat(), "schema_types": (p.structured_data or {}).get("schema_types", []),
             "external_domains": (p.links or {}).get("external_count", 0)} for p in rows]


async def search_businesses(session: AsyncSession, *, q: str | None, country: str | None, category_id: uuid.UUID | None, min_conf: float | None,
                            limit: int, offset: int, order: str = "recent") -> dict:
    stmt = select(BusinessNode)
    count_stmt = select(func.count(BusinessNode.id))
    conds = []
    if q:
        like = f"%{q.lower()}%"
        conds.append(func.lower(BusinessNode.domain).like(like) | func.lower(func.coalesce(BusinessNode.name, "")).like(like))
    if country:
        conds.append(BusinessNode.country_code == country.upper())
    if min_conf is not None:
        conds.append(BusinessNode.confidence >= min_conf)
    if category_id:
        cat = await session.get(CategoryNode, category_id)
        if cat:
            sub = select(CategoryNode.id).where((CategoryNode.path == cat.path) | CategoryNode.path.like(cat.path + "/%"))
            conds.append(BusinessNode.primary_category_id.in_(sub))
    if conds:
        stmt = stmt.where(*conds)
        count_stmt = count_stmt.where(*conds)
    stmt = stmt.order_by(BusinessNode.confidence.desc() if order == "confidence" else BusinessNode.created_at.desc()).offset(offset).limit(limit)
    rows = list((await session.scalars(stmt)).all())
    total = int(await session.scalar(count_stmt) or 0)
    cat_ids = {b.primary_category_id for b in rows if b.primary_category_id}
    cats = {c.id: c for c in (await session.scalars(select(CategoryNode).where(CategoryNode.id.in_(cat_ids)))).all()} if cat_ids else {}
    items = []
    for b in rows:
        d = business_summary(b)
        c = cats.get(b.primary_category_id)
        d["primary_category"] = {"id": str(c.id), "name": c.name, "path": c.path} if c else None
        items.append(d)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def graph_totals(session: AsyncSession) -> dict:
    b = int(await session.scalar(select(func.count(BusinessNode.id))) or 0)
    c = int(await session.scalar(select(func.count(CategoryNode.id)).where(CategoryNode.slug != ROOT_SLUG)) or 0)
    e = int(await session.scalar(select(func.count(GraphEdge.id))) or 0)
    avg_conf = float(await session.scalar(select(func.avg(cast(BusinessNode.confidence, Float)))) or 0)
    return {"businesses": b, "categories": c, "edges": e, "avg_confidence": avg_conf}
