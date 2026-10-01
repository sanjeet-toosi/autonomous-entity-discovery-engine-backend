"""The only component allowed to mutate the knowledge graph. Idempotent by construction
(unique domain, unique edges, upserts) and every mutation produces a change event."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessNode, CategoryNode, ChangeEvent, Evidence, GraphEdge
from app.events.publisher import emit
from app.events.types import Category, Event
from app.graph.neo4j_store import get_neo4j
from app.graph.taxonomy import ensure_path
from app.llm.interface import BusinessExtraction
from app.metrics import service as metrics


@dataclass
class WriteResult:
    business: BusinessNode
    created: bool
    categories: list[CategoryNode]
    edges_created: int
    classification_changed: bool


def _snapshot(b: BusinessNode) -> dict:
    return {
        "domain": b.domain, "name": b.name, "country_code": b.country_code, "region": b.region, "city": b.city,
        "confidence": b.confidence, "status": b.status, "primary_category_id": str(b.primary_category_id) if b.primary_category_id else None,
    }


async def upsert_edge(session: AsyncSession, *, source_type: str, source_id: uuid.UUID, target_type: str, target_id: uuid.UUID,
                      rel: str, confidence: float, source: str, run_id: uuid.UUID | None) -> bool:
    stmt = insert(GraphEdge).values(
        source_type=source_type, source_id=source_id, target_type=target_type, target_id=target_id, relationship_type=rel,
        confidence=confidence, evidence_count=1, source=source, run_id=run_id,
    ).on_conflict_do_update(
        constraint="uq_edge",
        set_={"confidence": confidence, "evidence_count": GraphEdge.evidence_count + 1, "updated_at": datetime.now(timezone.utc)},
    ).returning(GraphEdge.id, GraphEdge.evidence_count)
    row = (await session.execute(stmt)).one()
    created = row[1] == 1
    if created:
        session.add(ChangeEvent(run_id=run_id, event_type="EDGE_CREATED", entity_type="edge", entity_id=row[0],
                                after={"source": f"{source_type}:{source_id}", "target": f"{target_type}:{target_id}", "rel": rel}))
    return created


async def write_business(
    session: AsyncSession, *, domain: str, registrable: str, extraction: BusinessExtraction, page_url: str,
    evidence_items: list[tuple[str, str, float]], run_id: uuid.UUID | None, mission_id: uuid.UUID | None,
    source_type: str | None, source_reference: str | None, worker_id: str | None, metadata: dict,
) -> WriteResult:
    existing = await session.scalar(select(BusinessNode).where(BusinessNode.domain == domain))
    before = _snapshot(existing) if existing else None
    now = datetime.now(timezone.utc)

    chain = await ensure_path(session, extraction.category_path or ["Uncategorized"], run_id=run_id, mission_id=mission_id,
                              confidence=extraction.confidence)
    leaf = chain[-1] if chain else None

    if existing is None:
        b = BusinessNode(
            domain=domain, registrable_domain=registrable, name=extraction.name, description=extraction.description,
            country_code=extraction.country, region=extraction.region, city=extraction.city, business_type=extraction.entity_kind,
            confidence=extraction.confidence, primary_category_id=leaf.id if leaf else None, mission_id=mission_id,
            discovered_by_run_id=run_id, source_type=source_type, source_reference=source_reference, metadata_=metadata,
            first_seen_at=now, last_seen_at=now, last_verified_at=now,
        )
        session.add(b)
        await session.flush()
        created = True
        if leaf:
            leaf.business_count += 1
    else:
        b = existing
        created = False
        b.name = extraction.name or b.name
        b.description = extraction.description or b.description
        b.country_code = extraction.country or b.country_code
        b.region = extraction.region or b.region
        b.city = extraction.city or b.city
        b.confidence = max(b.confidence, extraction.confidence) if leaf and b.primary_category_id == leaf.id else extraction.confidence
        b.last_seen_at = now
        b.last_verified_at = now
        b.metadata_ = {**(b.metadata_ or {}), **metadata}
        if leaf and b.primary_category_id != leaf.id:
            if b.primary_category_id:
                old = await session.get(CategoryNode, b.primary_category_id)
                if old:
                    old.business_count = max(0, old.business_count - 1)
            b.primary_category_id = leaf.id
            leaf.business_count += 1

    classification_changed = bool(before) and before.get("primary_category_id") != (str(leaf.id) if leaf else None)

    edges_created = 0
    if leaf:
        if await upsert_edge(session, source_type="business", source_id=b.id, target_type="category", target_id=leaf.id,
                             rel="BELONGS_TO", confidence=extraction.confidence, source="classifier", run_id=run_id):
            edges_created += 1
    for child, parent in zip(chain[1:], chain[:-1]):
        if await upsert_edge(session, source_type="category", source_id=child.id, target_type="category", target_id=parent.id,
                             rel="SUBCATEGORY_OF", confidence=1.0, source="taxonomy", run_id=run_id):
            edges_created += 1

    for claim, value, conf in evidence_items[:12]:
        session.add(Evidence(subject_type="business", subject_id=b.id, claim=claim, source_url=page_url, evidence_type=_etype(claim),
                             evidence_value=value[:2000], confidence=conf, run_id=run_id))

    after = _snapshot(b)
    session.add(ChangeEvent(run_id=run_id, event_type="BUSINESS_CREATED" if created else "BUSINESS_UPDATED", entity_type="business",
                            entity_id=b.id, before=before, after=after))
    if classification_changed:
        session.add(ChangeEvent(run_id=run_id, event_type="CLASSIFICATION_CHANGED", entity_type="business", entity_id=b.id,
                                before={"primary_category_id": before.get("primary_category_id")}, after={"primary_category_id": after["primary_category_id"]}))
    await metrics.incr_many(
        session,
        {metrics.BUSINESS_CREATED if created else metrics.BUSINESS_UPDATED: 1, metrics.EDGE_CREATED: edges_created,
         metrics.CONFIDENCE_SUM: extraction.confidence},
        run_id=run_id, mission_id=mission_id,
    )
    path_names = " › ".join(c.name for c in chain)
    await emit(session, Event.BUSINESS_CREATED if created else Event.BUSINESS_UPDATED,
               f"{'Created' if created else 'Updated'} business node {domain}" + (f" → {path_names}" if path_names else ""),
               category=Category.GRAPH, run_id=run_id, mission_id=mission_id, worker_id=worker_id, domain=domain,
               payload={"business_id": str(b.id), "name": b.name, "category_path": [c.name for c in chain],
                        "confidence": extraction.confidence, "created": created, "edges_created": edges_created})

    # projection (best effort, outside the PG transaction semantics)
    await get_neo4j().upsert_business(
        {"domain": b.domain, "name": b.name, "country_code": b.country_code, "region": b.region, "city": b.city,
         "confidence": b.confidence, "pg_id": str(b.id)},
        [{"slug": c.slug, "name": c.name, "path": c.path, "depth": c.depth,
          "parent_slug": (chain[i - 1].slug if i > 0 else "business"), "leaf": c is leaf} for i, c in enumerate(chain)],
        extraction.confidence,
    )
    return WriteResult(business=b, created=created, categories=chain, edges_created=edges_created, classification_changed=classification_changed)


def _etype(claim: str) -> str:
    c = claim.lower()
    if "listing" in c:  # structured source (Google Places / OSM / registry) corroborating the page
        return "SOURCE_LISTING"
    if "schema" in c or "json-ld" in c:
        return "JSON_LD"
    if "meta" in c or "og:" in c:
        return "META"
    if "llm" in c or "model" in c:
        return "LLM_INFERENCE"
    if "link" in c:
        return "LINK"
    return "HTML_TEXT"


async def link_businesses(session: AsyncSession, source: BusinessNode, target_domains: list[str], run_id: uuid.UUID | None) -> int:
    """BUSINESS -LINKS_TO-> BUSINESS for outbound links that already resolve to graph nodes."""
    if not target_domains:
        return 0
    targets = list((await session.scalars(select(BusinessNode).where(BusinessNode.registrable_domain.in_(target_domains[:100])))).all())
    n = 0
    for t in targets:
        if t.id == source.id:
            continue
        if await upsert_edge(session, source_type="business", source_id=source.id, target_type="business", target_id=t.id,
                             rel="LINKS_TO", confidence=0.9, source="link_discovery", run_id=run_id):
            n += 1
    if n:
        await metrics.incr(session, metrics.EDGE_CREATED, n, run_id=run_id)
        await get_neo4j().link_businesses(source.domain, [t.domain for t in targets])
    return n
