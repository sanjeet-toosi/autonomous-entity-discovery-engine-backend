"""Discovery frontier: PostgreSQL-authoritative candidate store with idempotent inserts,
lease-based claiming (FOR UPDATE SKIP LOCKED) and retry/backoff."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import CandidateDomain
from app.frontier.normalizer import registrable_domain
from app.frontier.priority import compute_priority

RETRYABLE = {"timeout", "connection_error", "http_5xx", "http_429", "rate_limited"}


@dataclass
class CandidateInput:
    domain: str
    source_type: str
    source_reference: str | None = None
    mission_relevance: float = 0.5
    business_probability: float = 0.5
    source_quality: float = 0.5
    novelty: float = 0.5
    information_gain: float = 0.5
    metadata: dict = field(default_factory=dict)


@dataclass
class AddResult:
    added: int = 0
    duplicates: int = 0
    invalid: int = 0
    added_domains: list[str] = field(default_factory=list)


async def add_candidates(
    session: AsyncSession,
    mission_id: uuid.UUID,
    run_id: uuid.UUID | None,
    candidates: list[CandidateInput],
    *,
    blocked: set[str] | None = None,
    priority_weights: dict | None = None,
) -> AddResult:
    result = AddResult()
    blocked = blocked or set(get_settings().crawl_blocked_domains)
    rows: dict[str, dict] = {}
    for c in candidates:
        norm = registrable_domain(c.domain)
        if not norm or norm in blocked or any(norm.endswith("." + b) for b in blocked):
            result.invalid += 1
            continue
        if norm in rows:
            continue
        rows[norm] = {
            "mission_id": mission_id,
            "run_id": run_id,
            "domain": norm,
            "normalized_domain": norm,
            "source_type": c.source_type,
            "source_reference": (c.source_reference or "")[:1000] or None,
            "mission_relevance_score": c.mission_relevance,
            "business_probability": c.business_probability,
            "source_quality_score": c.source_quality,
            "novelty_score": c.novelty,
            "information_gain_score": c.information_gain,
            "priority": compute_priority(
                mission_relevance=c.mission_relevance, business_probability=c.business_probability,
                source_quality=c.source_quality, novelty=c.novelty, information_gain=c.information_gain,
                weights=priority_weights,
            ),
            "status": "NEW",
            "metadata_": c.metadata,
        }
    if not rows:
        return result
    stmt = insert(CandidateDomain).values(list(rows.values())).on_conflict_do_nothing(
        constraint="uq_candidate_mission_domain"
    ).returning(CandidateDomain.normalized_domain)
    inserted = [r[0] for r in (await session.execute(stmt)).all()]
    result.added = len(inserted)
    result.added_domains = inserted
    result.duplicates = len(rows) - len(inserted)
    return result


async def claim_next(session: AsyncSession, mission_id: uuid.UUID, run_id: uuid.UUID, worker_id: str) -> CandidateDomain | None:
    """Atomically lease the highest-priority claimable candidate for this mission."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    sub = (
        select(CandidateDomain.id)
        .where(
            CandidateDomain.mission_id == mission_id,
            CandidateDomain.status.in_(("NEW", "QUEUED", "RETRY")),
            (CandidateDomain.next_attempt_at.is_(None)) | (CandidateDomain.next_attempt_at <= now),
        )
        .order_by(CandidateDomain.priority.desc(), CandidateDomain.first_discovered_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(CandidateDomain)
        .where(CandidateDomain.id == sub.scalar_subquery())
        .values(
            status="PROCESSING", run_id=run_id, claimed_by_worker=worker_id, claimed_at=now,
            lease_until=now + timedelta(seconds=s.candidate_lease_seconds), last_attempt_at=now,
            attempt_count=CandidateDomain.attempt_count + 1,
        )
        .returning(CandidateDomain)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row


async def extend_lease(session: AsyncSession, candidate_id: uuid.UUID) -> None:
    s = get_settings()
    await session.execute(
        update(CandidateDomain).where(CandidateDomain.id == candidate_id).values(
            lease_until=datetime.now(timezone.utc) + timedelta(seconds=s.candidate_lease_seconds)
        )
    )


async def complete(session: AsyncSession, candidate_id: uuid.UUID, status: str, reason: str | None = None, meta: dict | None = None) -> None:
    values: dict = {
        "status": status, "processed_at": datetime.now(timezone.utc), "claimed_by_worker": None, "lease_until": None,
        "reject_reason": (reason or "")[:200] or None,
    }
    if meta:
        values["metadata_"] = func.coalesce(CandidateDomain.metadata_, text("'{}'::jsonb")).op("||")(meta)
    await session.execute(update(CandidateDomain).where(CandidateDomain.id == candidate_id).values(**values))


async def fail_or_retry(session: AsyncSession, cand: CandidateDomain, failure_type: str, detail: str) -> str:
    """Returns the resulting status (RETRY or FAILED)."""
    s = get_settings()
    if failure_type in RETRYABLE and cand.attempt_count < s.candidate_max_attempts:
        backoff = timedelta(minutes=5 * (2 ** (cand.attempt_count - 1)))
        await session.execute(
            update(CandidateDomain).where(CandidateDomain.id == cand.id).values(
                status="RETRY", next_attempt_at=datetime.now(timezone.utc) + backoff, claimed_by_worker=None,
                lease_until=None, reject_reason=f"{failure_type}: {detail}"[:200],
            )
        )
        return "RETRY"
    await complete(session, cand.id, "FAILED", f"{failure_type}: {detail}")
    return "FAILED"


async def release_expired_leases(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    s = get_settings()
    stmt = (
        update(CandidateDomain)
        .where(CandidateDomain.status == "PROCESSING", CandidateDomain.lease_until < now)
        .values(
            status=case((CandidateDomain.attempt_count >= s.candidate_max_attempts, "FAILED"), else_="RETRY"),
            claimed_by_worker=None, lease_until=None, reject_reason="lease_expired",
        )
    )
    res = await session.execute(stmt)
    return res.rowcount or 0


async def release_run_processing(session: AsyncSession, run_id: uuid.UUID) -> int:
    """On stop: return in-flight candidates to the queue so nothing is lost."""
    res = await session.execute(
        update(CandidateDomain)
        .where(CandidateDomain.run_id == run_id, CandidateDomain.status == "PROCESSING")
        .values(status="QUEUED", claimed_by_worker=None, lease_until=None, attempt_count=CandidateDomain.attempt_count - 1)
    )
    return res.rowcount or 0


async def counts(session: AsyncSession, mission_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None) -> dict[str, int]:
    q = select(CandidateDomain.status, func.count()).group_by(CandidateDomain.status)
    if mission_id:
        q = q.where(CandidateDomain.mission_id == mission_id)
    if run_id:
        q = q.where(CandidateDomain.run_id == run_id)
    rows = (await session.execute(q)).all()
    out = {k: 0 for k in ("NEW", "QUEUED", "PROCESSING", "PROCESSED", "REJECTED", "RETRY", "FAILED")}
    out.update({k: int(v) for k, v in rows})
    out["queued_total"] = out["NEW"] + out["QUEUED"] + out["RETRY"]
    out["total"] = sum(v for k, v in out.items() if k not in ("queued_total", "total"))
    return out


async def processing_count(session: AsyncSession, run_id: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    return int(
        await session.scalar(
            select(func.count()).where(
                CandidateDomain.run_id == run_id, CandidateDomain.status == "PROCESSING", CandidateDomain.lease_until > now
            )
        )
        or 0
    )
