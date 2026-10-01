"""CMS NPPES NPI Registry: US healthcare organisations (NPI-2) by taxonomy and state. Free, no key. The API
pages 200 at a time up to skip=1000 per query, so jobs are partitioned by (state, city) — statewide first,
then each major city — and paged with follow-up jobs."""
from __future__ import annotations

import httpx

from app.discovery.geography import COUNTRY_CITIES, US_STATE_CODES
from app.discovery.sources.base import JobResult, SourceHit
from app.llm.taxonomy_hints import source_vocab

NPPES = "https://npiregistry.cms.hhs.gov/api/"
PAGE = 200
MAX_SKIP = 1000


class NppesSource:
    key = "nppes"
    min_interval_seconds = 2

    def applies(self, parsed_objective: dict) -> tuple[bool, str]:
        if (parsed_objective.get("geography") or {}).get("country") != "US":
            return False, "US only"
        tax = source_vocab(parsed_objective.get("vertical", ""), "nppes")
        return (True, f"taxonomies: {', '.join(tax)}") if tax else (False, "not a US healthcare vertical")

    async def plan(self, session, parsed_objective, strategy_config, integration) -> list[dict]:
        geo = parsed_objective.get("geography") or {}
        tax = source_vocab(parsed_objective.get("vertical", ""), "nppes")
        regions = geo.get("regions") or list(US_STATE_CODES)
        cities = geo.get("cities") or []
        types = ["NPI-2"] + (["NPI-1"] if integration.config.get("include_individuals") else [])
        jobs: list[dict] = []
        for region in regions:
            code = US_STATE_CODES.get(region)
            if not code:
                continue
            city_list = cities or ["", *COUNTRY_CITIES.get("US", {}).get(region, [])]
            for t in tax:
                for et in types:
                    for city in city_list:
                        jobs.append({"state": code, "region": region, "city": city, "taxonomy": t, "enumeration_type": et, "skip": 0})
        return jobs

    async def execute(self, job: dict, integration, stats: dict) -> JobResult:
        params = {"version": "2.1", "taxonomy_description": job["taxonomy"], "state": job["state"], "enumeration_type": job["enumeration_type"],
                  "limit": PAGE, "skip": job["skip"], **({"city": job["city"]} if job["city"] else {})}
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.get(NPPES, params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("Errors"):
            raise RuntimeError(str(data["Errors"])[:200])
        results = data.get("results", [])
        hits: list[SourceHit] = []
        for rec in results:
            basic = rec.get("basic", {})
            name = basic.get("organization_name") or " ".join(x for x in (basic.get("first_name"), basic.get("last_name")) if x)
            loc = next((a for a in rec.get("addresses", []) if a.get("address_purpose") == "LOCATION"), None) or {}
            tax = [t.get("desc") for t in rec.get("taxonomies", []) if t.get("desc")]
            hits.append(SourceHit(
                source=self.key, external_id=str(rec.get("number")), name=name or None, website_url=None, phone=loc.get("telephone_number"),
                address=" ".join(x for x in (loc.get("address_1"), loc.get("address_2")) if x) or None, city=(loc.get("city") or "").title() or None,
                region=job["region"], postal_code=(loc.get("postal_code") or "")[:5] or None, country_code="US", categories=tax,
                raw={"enumeration_type": rec.get("enumeration_type"), "status": basic.get("status"), "taxonomies": tax[:5]},
            ))
        follow = []
        next_skip = job["skip"] + PAGE
        if len(results) == PAGE and next_skip <= MAX_SKIP:
            follow = [{**job, "skip": next_skip}]
        note = f"{len(results)} records" + (" (API cap of 1200 reached — narrow by city)" if len(results) == PAGE and next_skip > MAX_SKIP else "")
        return JobResult(hits=hits, follow_up_jobs=follow, note=note)

    async def test(self, integration) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(NPPES, params={"version": "2.1", "taxonomy_description": "Dentist", "state": "NY", "limit": 1})
        ok = r.status_code == 200 and "result_count" in r.json()
        return {"ok": ok, "message": "NPPES reachable" if ok else f"HTTP {r.status_code}"}

    def describe(self, job: dict) -> str:
        where = f"{job['city']}, {job['state']}" if job["city"] else job["state"]
        return f"nppes {job['taxonomy']} {job['enumeration_type']} {where} +{job['skip']}"
