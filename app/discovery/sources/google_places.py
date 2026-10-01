"""Google Places API (New) Text Search over a grid of cells covering the mission area. Each (cell, term) is a
job; a job that returns the 60-result cap splits its cell into four follow-up jobs (adaptive quadtree).
Paid: the operator enables it and supplies a key in the UI; a per-run request budget bounds the spend."""
from __future__ import annotations

import httpx

from app.discovery.geo import area_label, area_queries, cell_label, geocode, grid_cells, split_cell
from app.discovery.sources.base import JobResult, SourceHit
from app.llm.taxonomy_hints import search_terms

TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,places.internationalPhoneNumber,"
          "places.location,places.types,places.primaryType,places.addressComponents,places.businessStatus")
MAX_PER_QUERY = 60  # 3 pages × 20


class GooglePlacesSource:
    key = "google_places"
    min_interval_seconds = 1

    def applies(self, parsed_objective: dict) -> tuple[bool, str]:
        return True, "applies to every mission"

    async def plan(self, session, parsed_objective, strategy_config, integration) -> list[dict]:
        terms = search_terms(parsed_objective.get("vertical", "business"))
        cell_km = float(integration.config.get("cell_km") or 25)
        jobs: list[dict] = []
        for q in area_queries(parsed_objective.get("geography", {})):
            area = await geocode(session, q)
            if not area:
                continue
            for cell in grid_cells(area.bbox, cell_km):
                for term in terms:
                    jobs.append({"area": area_label(q), "cell": cell, "term": term, "depth": 0})
        return jobs

    async def execute(self, job: dict, integration, stats: dict) -> JobResult:
        budget = int(integration.config.get("max_requests_per_run") or 500)
        if stats.get("requests", 0) >= budget:
            return JobResult(hits=[], note=f"request budget {budget} reached")
        cell = job["cell"]
        body = {"textQuery": f"{job['term']} in {job['area']}", "pageSize": 20,
                "locationRestriction": {"rectangle": {"low": {"latitude": cell["south"], "longitude": cell["west"]},
                                                      "high": {"latitude": cell["north"], "longitude": cell["east"]}}}}
        headers = {"X-Goog-Api-Key": integration.api_key or "", "X-Goog-FieldMask": FIELDS + ",nextPageToken", "Content-Type": "application/json"}
        places: list[dict] = []
        token = None
        async with httpx.AsyncClient(timeout=30) as c:
            for _ in range(3):
                payload = {**body, **({"pageToken": token} if token else {})}
                r = await c.post(TEXT_SEARCH, json=payload, headers=headers)
                stats["requests"] = stats.get("requests", 0) + 1
                if r.status_code != 200:
                    raise RuntimeError(f"Google Places HTTP {r.status_code}: {r.text[:200]}")
                data = r.json()
                places.extend(data.get("places", []))
                token = data.get("nextPageToken")
                if not token:
                    break
        hits = [self._hit(p, job["term"]) for p in places if p.get("businessStatus", "OPERATIONAL") != "CLOSED_PERMANENTLY"]
        follow = []
        if len(places) >= MAX_PER_QUERY and job.get("depth", 0) < 6:
            follow = [{**job, "cell": c2, "depth": job.get("depth", 0) + 1} for c2 in split_cell(cell)]
        return JobResult(hits=hits, follow_up_jobs=follow, note=f"{len(places)} places" + (" — cell split" if follow else ""))

    def _hit(self, p: dict, term: str) -> SourceHit:
        comps = {c["types"][0]: c.get("longText") for c in p.get("addressComponents", []) if c.get("types")}
        loc = p.get("location") or {}
        return SourceHit(
            source=self.key, external_id=p["id"], name=(p.get("displayName") or {}).get("text"), website_url=p.get("websiteUri"),
            phone=p.get("internationalPhoneNumber"), address=p.get("formattedAddress"), city=comps.get("locality") or comps.get("sublocality"),
            region=comps.get("administrative_area_level_1"), postal_code=comps.get("postal_code"),
            country_code=next((c.get("shortText") for c in p.get("addressComponents", []) if "country" in c.get("types", [])), None),
            lat=loc.get("latitude"), lng=loc.get("longitude"), categories=[p.get("primaryType") or term, *p.get("types", [])[:5]],
            raw={"types": p.get("types", []), "primary_type": p.get("primaryType"), "term": term},
        )

    async def test(self, integration) -> dict:
        if not integration.api_key:
            return {"ok": False, "message": "no API key saved"}
        headers = {"X-Goog-Api-Key": integration.api_key, "X-Goog-FieldMask": "places.id,places.displayName", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(TEXT_SEARCH, json={"textQuery": "coffee shop in Manhattan", "pageSize": 1}, headers=headers)
        if r.status_code == 200:
            return {"ok": True, "message": "key accepted by Places API (New)"}
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        return {"ok": False, "message": f"HTTP {r.status_code}: {msg}"}

    def describe(self, job: dict) -> str:
        return f"places '{job['term']}' {cell_label(job['cell'])}"
