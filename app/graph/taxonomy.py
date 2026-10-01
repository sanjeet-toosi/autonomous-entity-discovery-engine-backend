"""Category hierarchy with materialized slug paths. Root is the implicit 'Business' node."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CategoryNode, ChangeEvent
from app.events.publisher import emit
from app.events.types import Category, Event
from app.metrics import service as metrics

ROOT_SLUG = "business"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", "and")).strip("-")
    return s[:80] or "unknown"


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().title().replace(" And ", " & ")[:120]


async def ensure_root(session: AsyncSession) -> CategoryNode:
    root = await session.scalar(select(CategoryNode).where(CategoryNode.slug == ROOT_SLUG))
    if root is None:
        root = CategoryNode(slug=ROOT_SLUG, name="Business", depth=0, path=ROOT_SLUG, created_by="system", confidence=1.0)
        session.add(root)
        await session.flush()
    return root


async def ensure_path(session: AsyncSession, names: list[str], *, run_id: uuid.UUID | None, mission_id=None, confidence: float = 0.8) -> list[CategoryNode]:
    """Return the chain of category nodes for a path like ['Food & Beverage','Restaurant'] creating
    missing ones. Category slugs are global (not nested) so 'restaurant' cannot be duplicated under
    two parents by accident; the first parent wins and taxonomy merges are a Brain concern."""
    parent = await ensure_root(session)
    chain: list[CategoryNode] = []
    for raw in names[:4]:
        name = clean_name(raw)
        if not name or name.lower() == "business":
            continue
        slug = slugify(name)
        node = await session.scalar(select(CategoryNode).where(CategoryNode.slug == slug))
        if node is None:
            node = CategoryNode(
                slug=slug, name=name, parent_id=parent.id, depth=parent.depth + 1, path=f"{parent.path}/{slug}",
                created_by="agent", confidence=confidence,
            )
            session.add(node)
            await session.flush()
            session.add(ChangeEvent(run_id=run_id, event_type="CATEGORY_CREATED", entity_type="category", entity_id=node.id,
                                    after={"name": name, "slug": slug, "parent": parent.slug, "path": node.path}))
            await metrics.incr(session, metrics.CATEGORY_CREATED, run_id=run_id, mission_id=mission_id)
            await emit(session, Event.CATEGORY_CREATED, f"Created category {name} under {parent.name}", category=Category.GRAPH,
                       run_id=run_id, mission_id=mission_id, payload={"category_id": str(node.id), "path": node.path})
        chain.append(node)
        parent = node
    return chain
