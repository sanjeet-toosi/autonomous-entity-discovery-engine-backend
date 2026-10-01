"""Deterministic fallback provider. Guarantees the pipeline keeps working with no model available
and gives the LLM providers a baseline to beat. Uses only the structured page signals."""
from __future__ import annotations

import json
import re
import time

from pydantic import BaseModel

from app.llm.interface import BusinessExtraction, MissionPlan, ReflectionOutput, Usage
from app.llm.taxonomy_hints import VERTICAL_HINTS, guess_vertical

_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
_COUNTRY_WORDS = {
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US", "america": "US", "canada": "CA", "united kingdom": "GB", "uk": "GB",
    "australia": "AU", "india": "IN", "germany": "DE", "france": "FR",
}


class HeuristicProvider:
    name = "heuristic"
    model = "rules-v1"

    async def complete_json(self, system: str, user: str, schema: type[BaseModel], *, max_tokens: int, effort: str):
        t0 = time.perf_counter()
        payload = _parse_user(user)
        if schema is MissionPlan:
            out = plan_mission(payload.get("prompt", user))
        elif schema is BusinessExtraction:
            out = extract_business(payload)
        elif schema is ReflectionOutput:
            out = reflect(payload)
        else:
            raise NotImplementedError(f"heuristic provider cannot produce {schema.__name__}")
        return out, Usage(self.name, self.model, latency_ms=int((time.perf_counter() - t0) * 1000))


def _parse_user(user: str) -> dict:
    # Tasks send JSON payloads to the provider; the heuristic provider reads them directly.
    try:
        return json.loads(user)
    except Exception:
        return {"prompt": user}


def plan_mission(prompt: str) -> MissionPlan:
    text = prompt.lower()
    vertical, root, keywords = guess_vertical(text)
    country = None
    for word, code in _COUNTRY_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            country = code
            break
    regions = [name.title() for name in _US_STATES if re.search(rf"\b{name}\b", text)]
    if regions and not country:
        country = "US"
    return MissionPlan(
        vertical=vertical,
        related_verticals=VERTICAL_HINTS.get(vertical, {}).get("related", []),
        category_root=root,
        geography={"country": country, "regions": regions, "cities": []},
        keywords=keywords,
        objective=f"maximize unique valid {vertical} domains",
        summary=f"Discover {vertical} businesses" + (f" in {country}" if country else "") + " continuously.",
    )


def extract_business(p: dict) -> BusinessExtraction:
    page = p.get("page", {})
    mission = p.get("mission", {})
    vertical = mission.get("vertical", "business")
    root = mission.get("category_root", "Business")
    keywords = [k.lower() for k in mission.get("keywords", [])]
    text = " ".join(
        str(x) for x in (page.get("title"), page.get("description"), page.get("text_excerpt", "")[:4000])
    ).lower()
    sd_types = [t.lower() for t in page.get("schema_types", [])]
    ext_links = page.get("external_domain_count", 0)
    evidence: list[str] = []
    score = 0.0

    if any(t in sd_types for t in ("restaurant", "foodestablishment", "cafeorcoffeeshop", "bakery", "barorpub")):
        score += 0.45
        evidence.append(f"schema.org type {sd_types}")
    elif any(t in sd_types for t in ("localbusiness", "organization", "store", "corporation", "medicalorganization")):
        score += 0.3
        evidence.append(f"schema.org type {sd_types}")
    if page.get("has_address") or page.get("has_phone"):
        score += 0.2
        evidence.append("contact details present")
    kw_hits = [k for k in keywords if k in text]
    if kw_hits:
        score += min(0.3, 0.06 * len(kw_hits))
        evidence.append(f"keywords: {', '.join(kw_hits[:5])}")
    if page.get("has_menu_link") or page.get("has_reservation_link"):
        score += 0.15
        evidence.append("menu/reservation link")

    parked = any(s in text for s in ("domain is for sale", "buy this domain", "parked free", "this domain may be for sale"))
    directory_signals = ext_links > 60 or any(s in text for s in ("find restaurants near", "browse listings", "best restaurants in", "top 10", "directory of"))
    kind = "business"
    if parked:
        kind, score = "parked", 0.0
    elif directory_signals and score < 0.6:
        kind = "directory"
    elif page.get("status_code", 200) >= 400:
        kind, score = "other", 0.0

    matches = bool(kw_hits) or any(vertical in t for t in sd_types)
    name = (page.get("og_site_name") or page.get("title") or "").split("|")[0].split(" - ")[0].strip()[:120] or None
    country, region, city = _geo(text, page)
    path: list[str] = []
    if kind == "business":
        specific = _specific_category(text, vertical)
        path = [root, vertical.title()] + ([specific] if specific else [])
        if not matches:
            path = ["Business", "Uncategorized"]
    conf = round(min(0.95, score), 2)
    return BusinessExtraction(
        is_business=kind == "business" and score >= 0.35,
        entity_kind=kind,
        name=name,
        description=(page.get("description") or "")[:300] or None,
        category_path=path,
        matches_vertical=matches and kind == "business",
        country=country,
        region=region,
        city=city,
        confidence=conf,
        evidence=evidence[:6],
        is_directory_of_vertical=kind == "directory" and bool(kw_hits),
    )


def _geo(text: str, page: dict) -> tuple[str | None, str | None, str | None]:
    region = None
    for name, code in _US_STATES.items():
        if re.search(rf"\b{name}\b", text) or re.search(rf",\s*{code}\s+\d{{5}}\b", text.upper()):
            region = name.title()
            break
    country = "US" if region or re.search(r"\b\d{5}(?:-\d{4})?\b", text) and re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", text) else None
    for word, code in _COUNTRY_WORDS.items():
        if word in text:
            country = country or code
    return country, region, page.get("city_hint")


def _specific_category(text: str, vertical: str) -> str | None:
    for label, terms in VERTICAL_HINTS.get(vertical, {}).get("subcategories", {}).items():
        if any(t in text for t in terms):
            return label
    return None


def reflect(p: dict) -> ReflectionOutput:
    lessons = []
    for src in p.get("sources", [])[:20]:
        if src["processed"] >= 10 and src["valid_rate"] < 0.15:
            lessons.append(
                {
                    "title": f"Deprioritise source {src['source']}",
                    "statement": f"{src['source']} produced {src['valid_rate']:.0%} valid businesses over {src['processed']} candidates.",
                    "lesson_type": "source_quality",
                    "confidence": 0.7,
                    "proposed_change": {"source_weights": {src["source"]: 0.3}},
                }
            )
        elif src["processed"] >= 10 and src["valid_rate"] > 0.6:
            lessons.append(
                {
                    "title": f"Prioritise source {src['source']}",
                    "statement": f"{src['source']} produced {src['valid_rate']:.0%} valid businesses over {src['processed']} candidates.",
                    "lesson_type": "source_quality",
                    "confidence": 0.75,
                    "proposed_change": {"source_weights": {src["source"]: 1.0}},
                }
            )
    for f in p.get("failures", [])[:5]:
        if f["count"] >= 10:
            lessons.append(
                {
                    "title": f"Recurring failure: {f['failure_type']}",
                    "statement": f"{f['failure_type']} occurred {f['count']} times; consider skipping or backing off earlier.",
                    "lesson_type": "failure",
                    "confidence": 0.6,
                    "proposed_change": None,
                }
            )
    return ReflectionOutput(summary=f"Heuristic reflection over {p.get('experience_count', 0)} experiences.", lessons=lessons[:5])
