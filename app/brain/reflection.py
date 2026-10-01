"""Reflection: experiences -> aggregated dataset -> proposed lessons -> DRAFT strategy candidates.
Production strategy never changes here; promotion is a separate, explicit step."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrainExperience, BrainFailure, BrainLesson, BrainSourceProfile
from app.brain import strategies
from app.evaluation.evaluator import evaluate_candidate
from app.events.publisher import emit
from app.events.types import Category, Event
from app.llm.router import get_router


async def build_dataset(session: AsyncSession, mission_id: uuid.UUID, vertical: str, since: datetime | None) -> tuple[dict, list[uuid.UUID]]:
    q = select(BrainExperience).where(BrainExperience.mission_id == mission_id, BrainExperience.reflected.is_(False)).order_by(BrainExperience.created_at.desc()).limit(2000)
    exps = list((await session.scalars(q)).all())
    ids = [e.id for e in exps]
    valid = sum(1 for e in exps if e.result.get("valid_business"))
    actions: dict[str, int] = {}
    for e in exps:
        for a in e.actions:
            actions[a] = actions.get(a, 0) + 1
    by_source: dict[tuple[str, str], dict] = {}
    for e in exps:
        k = (e.source_type or "?", e.source_reference or "?")
        d = by_source.setdefault(k, {"n": 0, "valid": 0, "new_candidates": 0, "ms": 0})
        d["n"] += 1
        d["valid"] += int(bool(e.result.get("valid_business")))
        d["new_candidates"] += int(e.result.get("new_candidates", 0))
        d["ms"] += e.duration_ms
    sources = sorted(
        [{"source_type": k[0], "source": k[1][:120], "processed": d["n"], "valid_rate": round(d["valid"] / d["n"], 3), "avg_new_candidates": round(d["new_candidates"] / d["n"], 2),
          "avg_ms": int(d["ms"] / d["n"])} for k, d in by_source.items() if d["n"] >= 3],
        key=lambda s: -s["processed"],
    )[:25]
    failures = (await session.execute(
        select(BrainFailure.failure_type, func.sum(BrainFailure.count)).group_by(BrainFailure.failure_type).order_by(func.sum(BrainFailure.count).desc()).limit(10)
    )).all()
    existing = list((await session.scalars(select(BrainLesson.title).where(BrainLesson.vertical == vertical, BrainLesson.status != "REJECTED").limit(50))).all())
    profiles = (await session.execute(
        select(BrainSourceProfile.source_type, BrainSourceProfile.source, BrainSourceProfile.valid_rate, BrainSourceProfile.novel_domain_rate, BrainSourceProfile.candidates_processed)
        .where(BrainSourceProfile.vertical == vertical, BrainSourceProfile.candidates_processed >= 5).order_by(BrainSourceProfile.candidates_processed.desc()).limit(25)
    )).all()
    dataset = {
        "vertical": vertical, "experience_count": len(exps), "valid_rate": round(valid / len(exps), 3) if exps else 0,
        "action_frequency": actions, "sources": sources,
        "failures": [{"failure_type": t, "count": int(c)} for t, c in failures],
        "source_profiles": [{"source_type": st, "source": s[:120], "valid_rate": round(v, 3), "novel_rate": round(nv, 3), "processed": n} for st, s, v, nv, n in profiles],
        "existing_lessons": existing,
    }
    return dataset, ids


async def reflect_mission(session: AsyncSession, mission_id: uuid.UUID, vertical: str, run_id=None, min_experiences: int = 50) -> dict:
    dataset, ids = await build_dataset(session, mission_id, vertical, since=None)
    if dataset["experience_count"] < min_experiences:
        return {"skipped": True, "reason": f"only {dataset['experience_count']} unreflected experiences (< {min_experiences})"}
    output, provider = await get_router().reflect(dataset, run_id=run_id)
    created = []
    for lp in output.lessons[:6]:
        if lp.title in dataset["existing_lessons"]:
            continue
        lesson = BrainLesson(vertical=vertical, title=lp.title[:300], statement=lp.statement, lesson_type=lp.lesson_type, confidence=lp.confidence,
                             proposed_change=lp.proposed_change, evidence={"dataset_summary": {k: dataset[k] for k in ("experience_count", "valid_rate")}, "provider": provider},
                             experience_count=dataset["experience_count"], source="reflection")
        session.add(lesson)
        await session.flush()
        await emit(session, Event.BRAIN_LESSON_CREATED, f"Lesson proposed: {lp.title}", category=Category.BRAIN, run_id=run_id, mission_id=mission_id,
                   payload={"lesson_id": str(lesson.id), "confidence": lp.confidence})
        entry = {"lesson_id": str(lesson.id), "title": lp.title, "candidate_version": None, "evaluation": None}
        if lp.proposed_change:
            v = await strategies.create_candidate_version(session, vertical, lp.proposed_change, rationale=lp.statement, lesson_id=lesson.id, run_id=run_id)
            ev = await evaluate_candidate(session, vertical, v)
            entry["candidate_version"] = v.label
            entry["evaluation"] = ev
        created.append(entry)
    if ids:
        await session.execute(update(BrainExperience).where(BrainExperience.id.in_(ids)).values(reflected=True))
    await emit(session, Event.REFLECTION_COMPLETE, f"Reflection over {dataset['experience_count']} experiences: {len(created)} lesson(s) proposed",
               category=Category.BRAIN, run_id=run_id, mission_id=mission_id, payload={"summary": output.summary, "lessons": len(created)})
    return {"skipped": False, "summary": output.summary, "lessons": created, "provider": provider}
