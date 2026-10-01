"""Mission Planner: natural language -> structured objective. Uses the LLM router's
plan_mission task and then normalizes the result against the vertical vocabulary so weak
models (or the heuristic fallback) still produce a canonical, well-formed mission."""
from __future__ import annotations

from app.llm.interface import MissionPlan
from app.llm.router import get_router
from app.llm.taxonomy_hints import VERTICAL_HINTS, guess_vertical


async def interpret(prompt: str) -> tuple[MissionPlan, str]:
    plan, provider = await get_router().plan_mission(prompt)
    return normalize_plan(plan, prompt), provider


def normalize_plan(plan: MissionPlan, prompt: str) -> MissionPlan:
    vertical = (plan.vertical or "").strip().lower()
    guessed, guessed_root, guessed_kw = guess_vertical(f"{prompt.lower()} {vertical}")
    if vertical in VERTICAL_HINTS:
        canonical = vertical
    elif guessed in VERTICAL_HINTS:
        canonical = guessed
    else:
        canonical = vertical.rstrip("s") or guessed or "business"
    plan.vertical = canonical
    hints = VERTICAL_HINTS.get(canonical)
    if hints:
        plan.category_root = hints["root"]
        plan.related_verticals = list(dict.fromkeys([*plan.related_verticals, *hints["related"]]))
        plan.keywords = list(dict.fromkeys([k.lower() for k in [*hints["keywords"], *plan.keywords]]))[:20]
    elif not plan.category_root or len(plan.category_root) < 4:
        plan.category_root = guessed_root or "Business"
    if not plan.keywords:
        plan.keywords = [canonical, *guessed_kw][:10]
    if plan.geography.country:
        plan.geography.country = plan.geography.country.upper()[:2]
    if not plan.objective or "business domains" in plan.objective:
        plan.objective = f"maximize unique valid {canonical} domains"
    if not plan.summary or plan.summary.strip().lower() == prompt.strip().lower():
        geo = f" in {plan.geography.country}" if plan.geography.country else ""
        plan.summary = f"Discover {canonical} businesses{geo} continuously."
    return plan


def mission_context(parsed: dict) -> dict:
    """Compact slice of the parsed objective given to the extraction model."""
    return {
        "vertical": parsed.get("vertical"),
        "related_verticals": parsed.get("related_verticals", []),
        "category_root": parsed.get("category_root", "Business"),
        "geography": parsed.get("geography", {}),
        "keywords": parsed.get("keywords", []),
    }
