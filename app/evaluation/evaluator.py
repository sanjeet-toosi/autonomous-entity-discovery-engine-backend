"""Offline strategy evaluation. Replays historical experiences against the candidate's source
weights to estimate discovery yield per request vs. the active strategy. Deliberately simple and
transparent: the result is stored on the experiment so the UI can show *why* a strategy looks better."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrainEvaluation, BrainExperience, BrainExperiment, BrainStrategy, BrainStrategyVersion


def _expected_yield(exps: list[BrainExperience], source_weights: dict[str, float]) -> tuple[float, float]:
    """Weight each experience by how likely the strategy would have scheduled it; return
    (valid businesses per 100 scheduled, requests per valid business)."""
    scheduled = valid = requests = 0.0
    for e in exps:
        w = source_weights.get(e.source_type or "", 0.5)
        w = max(0.05, min(1.0, w))
        scheduled += w
        valid += w * float(bool(e.result.get("valid_business")))
        requests += w * float((e.cost or {}).get("requests", 1))
    if scheduled == 0:
        return 0.0, 0.0
    return round(100 * valid / scheduled, 2), round(requests / valid, 2) if valid else 0.0


async def evaluate_candidate(session: AsyncSession, vertical: str, candidate: BrainStrategyVersion) -> dict:
    strategy = await session.get(BrainStrategy, candidate.strategy_id)
    baseline = await session.get(BrainStrategyVersion, strategy.active_version_id) if strategy and strategy.active_version_id else None
    exps = list((await session.scalars(select(BrainExperience).where(BrainExperience.vertical == vertical).order_by(BrainExperience.created_at.desc()).limit(3000))).all())
    base_cfg = (baseline.configuration if baseline else {}) or {}
    cand_cfg = candidate.configuration or {}
    b_yield, b_cost = _expected_yield(exps, base_cfg.get("source_weights", {}))
    c_yield, c_cost = _expected_yield(exps, cand_cfg.get("source_weights", {}))
    exp = BrainExperiment(vertical=vertical, hypothesis=candidate.rationale or candidate.label, candidate_version_id=candidate.id,
                          baseline_version_id=baseline.id if baseline else None, status="EVALUATED",
                          results={"experiences": len(exps), "baseline": {"yield_per_100": b_yield, "requests_per_valid": b_cost},
                                   "candidate": {"yield_per_100": c_yield, "requests_per_valid": c_cost}})
    session.add(exp)
    await session.flush()
    for metric, bv, cv in (("yield_per_100", b_yield, c_yield), ("requests_per_valid", b_cost, c_cost)):
        delta = ((cv - bv) / bv * 100) if bv else 0.0
        session.add(BrainEvaluation(experiment_id=exp.id, metric=metric, baseline_value=bv, candidate_value=cv, delta_pct=round(delta, 2)))
    candidate.status = "TESTING"
    candidate.metrics = {**(candidate.metrics or {}), "offline_yield_per_100": c_yield, "offline_requests_per_valid": c_cost, "experiment_id": str(exp.id)}
    return exp.results
