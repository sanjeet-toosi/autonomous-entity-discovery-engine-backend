"""Pre-aggregated hourly counters so dashboard statistics never scan millions of rows.

Every increment is written to three scopes: global, mission:<id>, run:<id>."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MetricHourly

RANGE_HOURS = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}

# Metric names (keep in sync with the frontend)
BUSINESS_CREATED = "business_created"
BUSINESS_UPDATED = "business_updated"
CATEGORY_CREATED = "category_created"
EDGE_CREATED = "edge_created"
CANDIDATES_ADDED = "candidates_added"
DUPLICATES_PREVENTED = "duplicates_prevented"
DOMAINS_CRAWLED = "domains_crawled"
PAGES_FETCHED = "pages_fetched"
DOMAINS_REJECTED = "domains_rejected"
CRAWL_ERRORS = "crawl_errors"
LLM_CALLS = "llm_calls"
LLM_COST_USD = "llm_cost_usd"
CONFIDENCE_SUM = "confidence_sum"
SEARCHES_EXECUTED = "searches_executed"
EXPERIENCES = "experiences"


def _bucket(ts: datetime | None = None) -> datetime:
    ts = ts or datetime.now(timezone.utc)
    return ts.replace(minute=0, second=0, microsecond=0)


def scopes(run_id: uuid.UUID | str | None, mission_id: uuid.UUID | str | None) -> list[str]:
    out = ["global"]
    if mission_id:
        out.append(f"mission:{mission_id}")
    if run_id:
        out.append(f"run:{run_id}")
    return out


async def incr(
    session: AsyncSession,
    metric: str,
    value: float = 1,
    *,
    run_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
) -> None:
    if not value:
        return
    bucket = _bucket()
    rows = [{"bucket": bucket, "scope": s, "metric": metric, "value": value} for s in scopes(run_id, mission_id)]
    stmt = insert(MetricHourly).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_metric_hourly", set_={"value": MetricHourly.value + stmt.excluded.value}
    )
    await session.execute(stmt)


async def incr_many(session: AsyncSession, counters: dict[str, float], **scope) -> None:
    for metric, value in counters.items():
        await incr(session, metric, value, **scope)


async def sum_range(session: AsyncSession, metrics: list[str], scope: str = "global", hours: int | None = 24) -> dict[str, float]:
    q = select(MetricHourly.metric, func.coalesce(func.sum(MetricHourly.value), 0.0)).where(
        MetricHourly.scope == scope, MetricHourly.metric.in_(metrics)
    )
    if hours is not None:
        q = q.where(MetricHourly.bucket >= _bucket() - timedelta(hours=hours))
    q = q.group_by(MetricHourly.metric)
    rows = (await session.execute(q)).all()
    out = {m: 0.0 for m in metrics}
    out.update({m: float(v) for m, v in rows})
    return out


async def series(session: AsyncSession, metric: str, scope: str = "global", hours: int = 24) -> list[dict]:
    since = _bucket() - timedelta(hours=hours - 1)
    q = (
        select(MetricHourly.bucket, MetricHourly.value)
        .where(MetricHourly.scope == scope, MetricHourly.metric == metric, MetricHourly.bucket >= since)
        .order_by(MetricHourly.bucket)
    )
    rows = (await session.execute(q)).all()
    by_bucket = {b: float(v) for b, v in rows}
    out = []
    for i in range(hours):
        b = since + timedelta(hours=i)
        out.append({"bucket": b.isoformat(), "value": by_bucket.get(b, 0.0)})
    return out
