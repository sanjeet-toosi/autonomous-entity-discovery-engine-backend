"""Candidate priority. Initial weights from the technical design; the Brain can override
them through the active strategy configuration (priority_weights)."""
from __future__ import annotations

DEFAULT_WEIGHTS = {
    "mission_relevance": 0.35,
    "business_probability": 0.25,
    "source_quality": 0.20,
    "novelty": 0.10,
    "information_gain": 0.10,
}


def compute_priority(
    *, mission_relevance: float, business_probability: float, source_quality: float, novelty: float,
    information_gain: float, weights: dict | None = None,
) -> float:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total = sum(w.values()) or 1.0
    p = (
        mission_relevance * w["mission_relevance"]
        + business_probability * w["business_probability"]
        + source_quality * w["source_quality"]
        + novelty * w["novelty"]
        + information_gain * w["information_gain"]
    ) / total
    return round(max(0.0, min(1.0, p)), 4)
