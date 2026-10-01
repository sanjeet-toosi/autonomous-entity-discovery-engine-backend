"""Statistics assembled from pre-aggregated metrics + cheap totals."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CandidateDomain, Run, WorkerHeartbeat
from app.frontier import service as frontier
from app.graph.queries import graph_totals
from app.metrics import service as m
from app.runs.service import runtime_seconds

CORE = [m.BUSINESS_CREATED, m.BUSINESS_UPDATED, m.CATEGORY_CREATED, m.EDGE_CREATED, m.CANDIDATES_ADDED, m.DUPLICATES_PREVENTED,
        m.DOMAINS_CRAWLED, m.PAGES_FETCHED, m.DOMAINS_REJECTED, m.CRAWL_ERRORS, m.LLM_CALLS, m.LLM_COST_USD, m.CONFIDENCE_SUM,
        m.SEARCHES_EXECUTED, m.EXPERIENCES]


def _hours(range_: str) -> int | None:
    if range_ in ("all", "total"):
        return None
    return m.RANGE_HOURS.get(range_, 24)


async def overview(session: AsyncSession, range_: str = "24h", mission_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None) -> dict:
    scope = f"run:{run_id}" if run_id else f"mission:{mission_id}" if mission_id else "global"
    hours = _hours(range_)
    change = await m.sum_range(session, CORE, scope=scope, hours=hours)
    scoped_total = await m.sum_range(session, CORE, scope=scope, hours=None)
    totals = await graph_totals(session)
    cand = await frontier.counts(session, mission_id=mission_id, run_id=run_id)
    workers = (await session.execute(select(WorkerHeartbeat.worker_type, WorkerHeartbeat.status, func.count()).group_by(WorkerHeartbeat.worker_type, WorkerHeartbeat.status))).all()
    active_workers = sum(int(n) for _, st, n in workers if st == "HEALTHY")
    crawled = change[m.DOMAINS_CRAWLED]
    valid = change[m.BUSINESS_CREATED] + change[m.BUSINESS_UPDATED]
    errors = change[m.CRAWL_ERRORS]
    denom = crawled + errors
    hrs = hours or max(1, (datetime.now(timezone.utc) - datetime(2026, 1, 1, tzinfo=timezone.utc)).total_seconds() / 3600)
    if scope == "global":
        cards = {
            "businesses": {"total": totals["businesses"], "change": change[m.BUSINESS_CREATED]},
            "categories": {"total": totals["categories"], "change": change[m.CATEGORY_CREATED]},
            "edges": {"total": totals["edges"], "change": change[m.EDGE_CREATED]},
            "candidates": {"total": cand["total"], "change": change[m.CANDIDATES_ADDED]},
        }
    else:
        cards = {
            "businesses": {"total": scoped_total[m.BUSINESS_CREATED], "change": change[m.BUSINESS_CREATED]},
            "categories": {"total": scoped_total[m.CATEGORY_CREATED], "change": change[m.CATEGORY_CREATED]},
            "edges": {"total": scoped_total[m.EDGE_CREATED], "change": change[m.EDGE_CREATED]},
            "candidates": {"total": cand["total"], "change": change[m.CANDIDATES_ADDED]},
        }
    return {
        "range": range_, "scope": scope, "cards": cards,
        "metrics": {
            "domains_crawled": crawled, "pages_fetched": change[m.PAGES_FETCHED], "valid_businesses": valid,
            "rejected_domains": change[m.DOMAINS_REJECTED], "duplicates_prevented": change[m.DUPLICATES_PREVENTED],
            "crawl_errors": errors, "error_rate": round(errors / denom, 4) if denom else 0.0,
            "duplicate_rate": round(change[m.DUPLICATES_PREVENTED] / (change[m.DUPLICATES_PREVENTED] + change[m.CANDIDATES_ADDED]), 4)
            if (change[m.DUPLICATES_PREVENTED] + change[m.CANDIDATES_ADDED]) else 0.0,
            "avg_confidence": round(change[m.CONFIDENCE_SUM] / valid, 4) if valid else totals["avg_confidence"],
            "businesses_per_hour": round(change[m.BUSINESS_CREATED] / hrs, 2), "domains_per_hour": round(crawled / hrs, 2),
            "new_candidates_per_hour": round(change[m.CANDIDATES_ADDED] / hrs, 2), "searches_executed": change[m.SEARCHES_EXECUTED],
            "llm_calls": change[m.LLM_CALLS], "llm_cost_usd": round(change[m.LLM_COST_USD], 4), "experiences": change[m.EXPERIENCES],
            "queue_size": cand["queued_total"], "processing": cand["PROCESSING"], "active_workers": active_workers,
        },
        "frontier": cand,
    }


async def run_snapshot(session: AsyncSession, run: Run) -> dict:
    ov = await overview(session, "all", run_id=run.id)
    cand = await frontier.counts(session, mission_id=run.mission_id)
    processing = list((await session.scalars(
        select(CandidateDomain).where(CandidateDomain.run_id == run.id, CandidateDomain.status == "PROCESSING").order_by(CandidateDomain.claimed_at.desc()).limit(20)
    )).all())
    return {
        "run_id": str(run.id), "status": run.status, "runtime_seconds": round(runtime_seconds(run), 1), "strategy_version": run.strategy_version,
        "frontier": {"queued": cand["queued_total"], "processing": cand["PROCESSING"], "processed": cand["PROCESSED"], "rejected": cand["REJECTED"], "failed": cand["FAILED"], "total": cand["total"]},
        "totals": {"businesses": ov["cards"]["businesses"]["total"], "categories": ov["cards"]["categories"]["total"], "edges": ov["cards"]["edges"]["total"],
                   "candidates": cand["total"]},
        "metrics": ov["metrics"],
        "processing": [{"domain": c.normalized_domain, "worker": c.claimed_by_worker, "claimed_at": c.claimed_at.isoformat() if c.claimed_at else None,
                        "source": c.source_type, "priority": c.priority} for c in processing],
        "cursor_state": run.config.get("cursor_state", {}),
    }
