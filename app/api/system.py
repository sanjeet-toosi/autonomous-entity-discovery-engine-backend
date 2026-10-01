from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import get_session
from app.core.redis import get_redis
from app.db.models import ActivityEvent, CandidateDomain, WorkerHeartbeat
from app.events.websocket import hub
from app.graph.neo4j_store import get_neo4j
from app.llm.router import get_router

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    s = get_settings()
    pg = redis = False
    try:
        await session.execute(text("SELECT 1"))
        pg = True
    except Exception:
        pass
    try:
        redis = (await get_redis().ping()) is True
    except Exception:
        pass
    neo = await get_neo4j().healthy()
    r = get_router()
    ollama = None
    if r.provider_name == "ollama":
        ollama = await r.primary.healthy()
    return {"api": "ok", "postgres": pg, "redis": redis, "neo4j": neo, "neo4j_enabled": get_neo4j().enabled,
            "llm": {"provider": r.provider_name, "model": r.model_name, "ollama_healthy": ollama, "daily_spend_cap_usd": s.llm_max_daily_spend_usd},
            "websocket_clients": hub.client_count, "environment": s.environment}


@router.get("/workers")
async def workers(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.worker_type, WorkerHeartbeat.worker_id))).all()
    by_type: dict[str, dict] = {}
    for w in rows:
        d = by_type.setdefault(w.worker_type, {"healthy": 0, "total": 0})
        d["total"] += 1
        d["healthy"] += int(w.status == "HEALTHY")
    return {"summary": by_type,
            "workers": [{"worker_id": w.worker_id, "worker_type": w.worker_type, "status": w.status, "hostname": w.hostname, "pid": w.pid, "current_job": w.current_job,
                         "jobs_completed": w.jobs_completed, "jobs_failed": w.jobs_failed, "started_at": w.started_at.isoformat(), "last_heartbeat": w.last_heartbeat.isoformat()} for w in rows]}


@router.get("/queues")
async def queues(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(CandidateDomain.mission_id, CandidateDomain.status, func.count()).group_by(CandidateDomain.mission_id, CandidateDomain.status))).all()
    out: dict[str, dict[str, int]] = {}
    for mid, st, n in rows:
        out.setdefault(str(mid), {})[st] = int(n)
    return {"frontier_by_mission": out}


@router.get("/errors")
async def errors(session: AsyncSession = Depends(get_session), limit: int = Query(100, le=500)):
    rows = (await session.scalars(select(ActivityEvent).where(ActivityEvent.category == "errors").order_by(desc(ActivityEvent.id)).limit(limit))).all()
    return [{"id": e.id, "event": e.event_type, "level": e.level, "domain": e.domain, "message": e.message, "worker_id": e.worker_id,
             "run_id": str(e.run_id) if e.run_id else None, "timestamp": e.created_at.isoformat()} for e in rows]


@router.get("/config")
async def config():
    s = get_settings()
    return {"crawl": {"user_agent": s.crawl_user_agent, "per_domain_rpm": s.crawl_per_domain_rpm, "global_concurrency": s.crawl_global_concurrency,
                      "max_pages_per_domain": s.crawl_max_pages_per_domain, "respect_robots": s.crawl_respect_robots, "blocked_domains": s.crawl_blocked_domains},
            "workers": {"concurrency": s.worker_concurrency, "lease_seconds": s.candidate_lease_seconds, "max_attempts": s.candidate_max_attempts},
            "llm": {"provider": get_router().provider_name, "model": get_router().model_name, "max_calls_per_minute": s.llm_max_calls_per_minute}}
