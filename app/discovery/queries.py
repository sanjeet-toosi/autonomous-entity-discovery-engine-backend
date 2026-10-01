"""Deterministic, resumable search-query plan derived from mission + strategy."""
from __future__ import annotations

from app.discovery.geography import expansions
from app.llm.taxonomy_hints import search_terms


def build_query_plan(parsed_objective: dict, strategy_config: dict) -> list[str]:
    vertical = parsed_objective.get("vertical", "business")
    terms = search_terms(vertical)
    templates = strategy_config.get("search_query_templates", [])
    pairs = expansions(parsed_objective.get("geography", {}))
    queries: list[str] = []
    seen: set[str] = set()
    # Interleave: every region gets its first template before any region gets its second,
    # so early discovery is geographically diverse. Umbrella verticals expand into concrete
    # search terms (plumber, electrician, ...) so queries hit real businesses, not generic pages.
    for template in templates:
        needs_city = "{city}" in template
        for region, city in pairs:
            if needs_city and not city:
                continue
            if not needs_city and city:
                continue
            for term in terms:
                q = template.format(vertical=term, region=region, city=city).replace("  ", " ").strip()
                if q and q not in seen:
                    seen.add(q)
                    queries.append(q)
    return queries
