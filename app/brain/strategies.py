"""Strategies = versioned, evaluable discovery playbooks per vertical. Workers read the ACTIVE
version; reflection proposes DRAFT versions; promotion is explicit (API) never automatic."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrainMemory, BrainStrategy, BrainStrategyVersion, ChangeEvent
from app.events.publisher import emit
from app.events.types import Category, Event
from app.llm.taxonomy_hints import VERTICAL_HINTS

DEFAULT_CONFIG = {
    "max_pages_per_domain": 3,
    "follow_paths": ["/about", "/about-us", "/locations", "/menu", "/contact", "/contact-us"],
    "follow_path_keywords": ["about", "location", "menu", "contact"],
    "source_weights": {"search": 0.8, "outbound_link": 0.5, "directory_link": 0.9, "sitemap": 0.7, "seed": 1.0},
    "priority_weights": {"mission_relevance": 0.35, "business_probability": 0.25, "source_quality": 0.20, "novelty": 0.10, "information_gain": 0.10},
    "search_query_templates": [
        "{vertical} in {region}", "best {vertical}s in {city} {region}", "{vertical} association {region}",
        "{vertical} directory {region}", "family owned {vertical} {region}", "{vertical} group locations {region}",
        "new {vertical} opening {region} 2026", "{vertical} {city} official website",
    ],
    "min_business_confidence": 0.35,
    "link_discovery_max_domains": 40,
    "outbound_relevance_keywords": [],
}

DEFAULT_RULES = [
    {"step": 1, "action": "fetch_homepage", "why": "Homepage carries title/meta/structured data"},
    {"step": 2, "action": "inspect_structured_data", "why": "schema.org types are strong evidence"},
    {"step": 3, "action": "follow_key_paths", "why": "about/locations/menu/contact confirm entity + geography"},
    {"step": 4, "action": "classify", "why": "LLM + heuristic classification into taxonomy path"},
    {"step": 5, "action": "discover_outbound_domains", "why": "businesses link to peers, suppliers and directories"},
]

SEED_MEMORIES = [
    ("semantic", "*", "schema_org_strong_evidence", {"statement": "schema.org LocalBusiness/Restaurant JSON-LD is strong evidence of a real business."}, 0.9),
    ("semantic", "*", "locations_page_multi_location", {"statement": "A /locations page usually indicates a multi-location brand; each location rarely has its own domain."}, 0.75),
    ("semantic", "*", "directories_high_yield", {"statement": "Directory/aggregator pages are not businesses but are high-yield discovery sources for outbound domains."}, 0.85),
    ("semantic", "*", "parked_domain_signals", {"statement": "'Domain for sale' / registrar landing pages are parked; reject without LLM call."}, 0.95),
    ("semantic", "*", "social_links_noise", {"statement": "Outbound links to social networks, map providers and builders carry no discovery value."}, 0.9),
    ("procedural", "*", "default_playbook", {"steps": DEFAULT_RULES}, 0.8),
]


async def ensure_seed_memories(session: AsyncSession) -> None:
    for mtype, vertical, key, content, conf in SEED_MEMORIES:
        exists = await session.scalar(select(BrainMemory).where(BrainMemory.memory_type == mtype, BrainMemory.vertical == vertical, BrainMemory.key == key))
        if not exists:
            session.add(BrainMemory(memory_type=mtype, vertical=vertical, key=key, content=content, confidence=conf, source="seed"))


async def ensure_strategy(session: AsyncSession, vertical: str) -> tuple[BrainStrategy, BrainStrategyVersion]:
    name = f"{vertical}_discovery"
    strategy = await session.scalar(select(BrainStrategy).where(BrainStrategy.vertical == vertical, BrainStrategy.name == name))
    if strategy is None:
        strategy = BrainStrategy(name=name, vertical=vertical, description=f"Discovery playbook for {vertical} businesses")
        session.add(strategy)
        await session.flush()
        cfg = {**DEFAULT_CONFIG, "outbound_relevance_keywords": VERTICAL_HINTS.get(vertical, {}).get("keywords", [vertical])}
        v1 = BrainStrategyVersion(strategy_id=strategy.id, version=1, label=f"{vertical}-v1", status="ACTIVE", configuration=cfg,
                                  rules=DEFAULT_RULES, rationale="Bootstrap strategy", promoted_at=datetime.now(timezone.utc))
        session.add(v1)
        await session.flush()
        strategy.active_version_id = v1.id
        await session.flush()
        return strategy, v1
    active = await session.get(BrainStrategyVersion, strategy.active_version_id) if strategy.active_version_id else None
    if active is None:
        active = await session.scalar(select(BrainStrategyVersion).where(BrainStrategyVersion.strategy_id == strategy.id).order_by(BrainStrategyVersion.version.desc()).limit(1))
    return strategy, active


async def active_config(session: AsyncSession, vertical: str) -> tuple[dict, str]:
    _, v = await ensure_strategy(session, vertical)
    return {**DEFAULT_CONFIG, **(v.configuration or {})}, v.label


async def create_candidate_version(session: AsyncSession, vertical: str, config_delta: dict, rationale: str, lesson_id: uuid.UUID | None, run_id=None) -> BrainStrategyVersion:
    strategy, active = await ensure_strategy(session, vertical)
    latest = await session.scalar(select(BrainStrategyVersion.version).where(BrainStrategyVersion.strategy_id == strategy.id).order_by(BrainStrategyVersion.version.desc()).limit(1))
    version = (latest or 0) + 1
    cfg = _merge(dict(active.configuration or {}), config_delta)
    v = BrainStrategyVersion(strategy_id=strategy.id, version=version, label=f"{vertical}-v{version}", status="DRAFT", configuration=cfg,
                             rules=active.rules, rationale=rationale, derived_from_lesson_id=lesson_id)
    session.add(v)
    await session.flush()
    await emit(session, Event.STRATEGY_CANDIDATE_CREATED, f"Strategy candidate {v.label} created: {rationale[:120]}", category=Category.BRAIN,
               run_id=run_id, payload={"version_id": str(v.id), "label": v.label})
    return v


def _merge(base: dict, delta: dict) -> dict:
    for k, val in delta.items():
        if isinstance(val, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **val}
        else:
            base[k] = val
    return base


async def promote(session: AsyncSession, version_id: uuid.UUID) -> BrainStrategyVersion:
    v = await session.get(BrainStrategyVersion, version_id)
    if not v:
        raise ValueError("version not found")
    strategy = await session.get(BrainStrategy, v.strategy_id)
    if strategy.active_version_id and strategy.active_version_id != v.id:
        old = await session.get(BrainStrategyVersion, strategy.active_version_id)
        if old:
            old.status = "RETIRED"
    v.status = "ACTIVE"
    v.promoted_at = datetime.now(timezone.utc)
    strategy.active_version_id = v.id
    session.add(ChangeEvent(event_type="STRATEGY_PROMOTED", entity_type="strategy_version", entity_id=v.id, after={"label": v.label}))
    await emit(session, Event.STRATEGY_PROMOTED, f"Promoted strategy {v.label}", category=Category.BRAIN, payload={"version_id": str(v.id)})
    return v


async def reject(session: AsyncSession, version_id: uuid.UUID, reason: str | None = None) -> BrainStrategyVersion:
    v = await session.get(BrainStrategyVersion, version_id)
    if not v:
        raise ValueError("version not found")
    v.status = "REJECTED"
    v.metrics = {**(v.metrics or {}), "rejection_reason": reason}
    session.add(ChangeEvent(event_type="STRATEGY_REJECTED", entity_type="strategy_version", entity_id=v.id, after={"label": v.label, "reason": reason}))
    await emit(session, Event.STRATEGY_REJECTED, f"Rejected strategy {v.label}", category=Category.BRAIN, payload={"version_id": str(v.id)})
    return v
