from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.brain import strategies
from app.brain.reflection import reflect_mission
from app.core.database import get_session
from app.core.exceptions import NotFound
from app.db.models import (BrainEvaluation, BrainExperience, BrainExperiment, BrainFailure, BrainLesson, BrainMemory, BrainSourceProfile,
                           BrainStrategy, BrainStrategyVersion, Mission, ModelInvocation)

router = APIRouter(prefix="/brain", tags=["brain"])


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    verticals = (await session.execute(select(BrainExperience.vertical, func.count()).group_by(BrainExperience.vertical))).all()
    out = []
    for vertical, n in verticals:
        if not vertical:
            continue
        strat = await session.scalar(select(BrainStrategy).where(BrainStrategy.vertical == vertical))
        active = await session.get(BrainStrategyVersion, strat.active_version_id) if strat and strat.active_version_id else None
        validated = int(await session.scalar(select(func.count()).where(BrainLesson.vertical == vertical, BrainLesson.status == "VALIDATED")) or 0)
        proposed = int(await session.scalar(select(func.count()).where(BrainLesson.vertical == vertical, BrainLesson.status == "PROPOSED")) or 0)
        experiments = int(await session.scalar(select(func.count()).where(BrainExperiment.vertical == vertical, BrainExperiment.status.in_(("PENDING", "EVALUATED")))) or 0)
        valid = int(await session.scalar(select(func.count()).where(BrainExperience.vertical == vertical, BrainExperience.result["valid_business"].as_boolean().is_(True))) or 0)
        out.append({"vertical": vertical, "experience_count": int(n), "valid_businesses": valid, "valid_rate": round(valid / n, 3) if n else 0,
                    "active_strategy": active.label if active else None, "validated_lessons": validated, "proposed_lessons": proposed, "active_experiments": experiments,
                    "knowledge_confidence": "high" if n > 1000 else "medium" if n > 100 else "low"})
    memories = int(await session.scalar(select(func.count(BrainMemory.id))) or 0)
    llm = (await session.execute(select(ModelInvocation.provider, ModelInvocation.model, func.count(), func.sum(ModelInvocation.cost_usd),
                                        func.avg(ModelInvocation.latency_ms)).group_by(ModelInvocation.provider, ModelInvocation.model))).all()
    return {"verticals": out, "memories": memories,
            "models": [{"provider": p, "model": m, "calls": int(c), "cost_usd": round(float(cost or 0), 4), "avg_latency_ms": int(lat or 0)} for p, m, c, cost, lat in llm]}


@router.get("/memories")
async def memories(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(BrainMemory).order_by(BrainMemory.memory_type, BrainMemory.key))).all()
    return [{"id": str(m.id), "memory_type": m.memory_type, "vertical": m.vertical, "key": m.key, "content": m.content, "confidence": m.confidence,
             "source": m.source, "created_at": m.created_at.isoformat()} for m in rows]


@router.get("/lessons")
async def lessons(session: AsyncSession = Depends(get_session), status: str | None = None, limit: int = Query(100, le=500)):
    stmt = select(BrainLesson).order_by(desc(BrainLesson.created_at)).limit(limit)
    if status:
        stmt = stmt.where(BrainLesson.status == status)
    rows = (await session.scalars(stmt)).all()
    return [{"id": str(l.id), "vertical": l.vertical, "title": l.title, "statement": l.statement, "lesson_type": l.lesson_type, "status": l.status,
             "confidence": l.confidence, "evidence": l.evidence, "proposed_change": l.proposed_change, "experience_count": l.experience_count,
             "source": l.source, "created_at": l.created_at.isoformat()} for l in rows]


@router.post("/lessons/{lesson_id}/status", dependencies=[Depends(require_api_key)])
async def set_lesson_status(lesson_id: uuid.UUID, status: str = Query(pattern="^(VALIDATED|REJECTED|PROPOSED)$"), session: AsyncSession = Depends(get_session)):
    l = await session.get(BrainLesson, lesson_id)
    if not l:
        raise NotFound("lesson not found")
    l.status = status
    return {"id": str(l.id), "status": l.status}


@router.get("/strategies")
async def list_strategies(session: AsyncSession = Depends(get_session)):
    strats = (await session.scalars(select(BrainStrategy).order_by(BrainStrategy.vertical))).all()
    out = []
    for s in strats:
        versions = (await session.scalars(select(BrainStrategyVersion).where(BrainStrategyVersion.strategy_id == s.id).order_by(desc(BrainStrategyVersion.version)))).all()
        out.append({"id": str(s.id), "name": s.name, "vertical": s.vertical, "description": s.description, "active_version_id": str(s.active_version_id) if s.active_version_id else None,
                    "versions": [{"id": str(v.id), "version": v.version, "label": v.label, "status": v.status, "configuration": v.configuration, "rules": v.rules,
                                  "metrics": v.metrics, "rationale": v.rationale, "derived_from_lesson_id": str(v.derived_from_lesson_id) if v.derived_from_lesson_id else None,
                                  "created_at": v.created_at.isoformat(), "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None} for v in versions]})
    return out


@router.post("/strategies/versions/{version_id}/promote", dependencies=[Depends(require_api_key)])
async def promote(version_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    v = await strategies.promote(session, version_id)
    return {"id": str(v.id), "status": v.status, "label": v.label}


@router.post("/strategies/versions/{version_id}/reject", dependencies=[Depends(require_api_key)])
async def reject(version_id: uuid.UUID, session: AsyncSession = Depends(get_session), reason: str | None = None):
    v = await strategies.reject(session, version_id, reason)
    return {"id": str(v.id), "status": v.status, "label": v.label}


@router.get("/sources")
async def sources(session: AsyncSession = Depends(get_session), vertical: str | None = None, limit: int = Query(100, le=500), order: str = "processed"):
    stmt = select(BrainSourceProfile).limit(limit)
    if vertical:
        stmt = stmt.where(BrainSourceProfile.vertical == vertical)
    stmt = stmt.order_by(desc(BrainSourceProfile.score) if order == "score" else desc(BrainSourceProfile.candidates_processed))
    rows = (await session.scalars(stmt)).all()
    return [{"id": str(p.id), "source_type": p.source_type, "source": p.source, "vertical": p.vertical, "candidates_seen": p.candidates_seen,
             "candidates_processed": p.candidates_processed, "valid_businesses": p.valid_businesses, "valid_rate": round(p.valid_rate, 3),
             "duplicate_rate": round(p.duplicate_rate, 3), "novel_domain_rate": round(p.novel_domain_rate, 3), "avg_yield": round(p.avg_yield, 2),
             "failures": p.failures, "score": round(p.score, 3), "updated_at": p.updated_at.isoformat()} for p in rows]


@router.get("/failures")
async def failures(session: AsyncSession = Depends(get_session), limit: int = Query(100, le=500)):
    summary = (await session.execute(select(BrainFailure.failure_type, func.sum(BrainFailure.count), func.count()).group_by(BrainFailure.failure_type)
                                     .order_by(desc(func.sum(BrainFailure.count))))).all()
    recent = (await session.scalars(select(BrainFailure).order_by(desc(BrainFailure.updated_at)).limit(limit))).all()
    return {"summary": [{"failure_type": t, "occurrences": int(n), "domains": int(d)} for t, n, d in summary],
            "recent": [{"failure_type": f.failure_type, "domain": f.domain, "url": f.url, "detail": f.detail, "count": f.count, "updated_at": f.updated_at.isoformat()} for f in recent]}


@router.get("/experiments")
async def experiments(session: AsyncSession = Depends(get_session), limit: int = Query(50, le=200)):
    rows = (await session.scalars(select(BrainExperiment).order_by(desc(BrainExperiment.created_at)).limit(limit))).all()
    out = []
    for e in rows:
        evals = (await session.scalars(select(BrainEvaluation).where(BrainEvaluation.experiment_id == e.id))).all()
        cand = await session.get(BrainStrategyVersion, e.candidate_version_id)
        base = await session.get(BrainStrategyVersion, e.baseline_version_id) if e.baseline_version_id else None
        out.append({"id": str(e.id), "vertical": e.vertical, "hypothesis": e.hypothesis, "status": e.status, "decision": e.decision, "results": e.results,
                    "candidate": {"id": str(cand.id), "label": cand.label, "status": cand.status} if cand else None,
                    "baseline": {"id": str(base.id), "label": base.label} if base else None,
                    "evaluations": [{"metric": v.metric, "baseline": v.baseline_value, "candidate": v.candidate_value, "delta_pct": v.delta_pct} for v in evals],
                    "created_at": e.created_at.isoformat()})
    return out


@router.get("/experiences")
async def experiences(session: AsyncSession = Depends(get_session), limit: int = Query(50, le=500), mission_id: uuid.UUID | None = None):
    stmt = select(BrainExperience).order_by(desc(BrainExperience.created_at)).limit(limit)
    if mission_id:
        stmt = stmt.where(BrainExperience.mission_id == mission_id)
    rows = (await session.scalars(stmt)).all()
    return [{"id": str(e.id), "domain": e.domain, "vertical": e.vertical, "source_type": e.source_type, "source_reference": e.source_reference,
             "strategy_version": e.strategy_version, "actions": e.actions, "result": e.result, "cost": e.cost, "duration_ms": e.duration_ms,
             "run_id": str(e.run_id) if e.run_id else None, "created_at": e.created_at.isoformat()} for e in rows]


@router.post("/reflect", dependencies=[Depends(require_api_key)])
async def trigger_reflection(mission_id: uuid.UUID, session: AsyncSession = Depends(get_session), min_experiences: int = 1):
    mission = await session.get(Mission, mission_id)
    if not mission:
        raise NotFound("mission not found")
    return await reflect_mission(session, mission.id, mission.parsed_objective.get("vertical", "business"), min_experiences=min_experiences)
