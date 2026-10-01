"""Source profiles: learned quality of every discovery source (search query, referring domain...)."""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrainSourceProfile


def _score(valid_rate: float, novel_rate: float, dup_rate: float, avg_yield: float, processed: int) -> float:
    confidence = min(1.0, processed / 30)  # shrink toward prior when few samples
    raw = 0.5 * valid_rate + 0.25 * novel_rate + 0.15 * min(1.0, avg_yield / 10) - 0.1 * dup_rate
    return round(0.5 * (1 - confidence) + max(0.0, min(1.0, raw)) * confidence, 4)


async def observe_candidates(session: AsyncSession, source_type: str, source: str, vertical: str, seen: int, novel: int, duplicates: int) -> None:
    stmt = insert(BrainSourceProfile).values(source_type=source_type, source=source[:300], vertical=vertical, candidates_seen=seen,
                                             novel_domains=novel, duplicates=duplicates)
    stmt = stmt.on_conflict_do_update(constraint="uq_source_profile", set_={
        "candidates_seen": BrainSourceProfile.candidates_seen + seen, "novel_domains": BrainSourceProfile.novel_domains + novel,
        "duplicates": BrainSourceProfile.duplicates + duplicates,
    })
    await session.execute(stmt)
    await _recompute(session, source_type, source, vertical)


async def observe_outcome(session: AsyncSession, source_type: str | None, source: str | None, vertical: str, *, valid: bool, failed: bool, downstream: int) -> None:
    if not source_type or not source:
        return
    stmt = insert(BrainSourceProfile).values(source_type=source_type, source=source[:300], vertical=vertical, candidates_processed=1,
                                             valid_businesses=int(valid), failures=int(failed), downstream_candidates=downstream)
    stmt = stmt.on_conflict_do_update(constraint="uq_source_profile", set_={
        "candidates_processed": BrainSourceProfile.candidates_processed + 1,
        "valid_businesses": BrainSourceProfile.valid_businesses + int(valid),
        "failures": BrainSourceProfile.failures + int(failed),
        "downstream_candidates": BrainSourceProfile.downstream_candidates + downstream,
    })
    await session.execute(stmt)
    await _recompute(session, source_type, source, vertical)


async def _recompute(session: AsyncSession, source_type: str, source: str, vertical: str) -> None:
    from sqlalchemy import select

    p = await session.scalar(select(BrainSourceProfile).where(BrainSourceProfile.source_type == source_type, BrainSourceProfile.source == source[:300], BrainSourceProfile.vertical == vertical))
    if not p:
        return
    p.valid_rate = p.valid_businesses / p.candidates_processed if p.candidates_processed else 0.0
    p.duplicate_rate = p.duplicates / p.candidates_seen if p.candidates_seen else 0.0
    p.novel_domain_rate = p.novel_domains / p.candidates_seen if p.candidates_seen else 0.0
    p.avg_yield = p.downstream_candidates / p.candidates_processed if p.candidates_processed else 0.0
    p.score = _score(p.valid_rate, p.novel_domain_rate, p.duplicate_rate, p.avg_yield, p.candidates_processed)


async def quality_for(session: AsyncSession, source_type: str, source: str | None, vertical: str, default: float) -> float:
    if not source:
        return default
    from sqlalchemy import select

    p = await session.scalar(select(BrainSourceProfile).where(BrainSourceProfile.source_type == source_type, BrainSourceProfile.source == source[:300], BrainSourceProfile.vertical == vertical))
    return p.score if p and p.candidates_processed >= 5 else default
