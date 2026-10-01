"""OpenStreetMap via the Overpass API: every element tagged with the vertical's OSM tags inside the mission
area. Free, no key, polite rate limits (one query per worker tick)."""
from __future__ import annotations

import asyncio

import httpx

from app.discovery.geo import area_label, area_queries, geocode, overpass_area_id
from app.discovery.sources.base import JobResult, SourceHit
from app.llm.taxonomy_hints import source_vocab

OVERPASS = "https://overpass-api.de/api/interpreter"
# Overpass answers 406 to the default httpx agent and to agents carrying a URL; a short product token works.
OSM_UA = "BusinessGraphBot/0.1"


class OsmOverpassSource:
    key = "osm_overpass"
    min_interval_seconds = 25  # public Overpass sheds load at ~2 heavy queries/min

    def applies(self, parsed_objective: dict) -> tuple[bool, str]:
        tags = source_vocab(parsed_objective.get("vertical", ""), "osm")
        return (True, f"{len(tags)} OSM tags") if tags else (False, "no OSM tag mapping for this vertical")

    async def plan(self, session, parsed_objective, strategy_config, integration) -> list[dict]:
        tags = source_vocab(parsed_objective.get("vertical", ""), "osm")
        jobs: list[dict] = []
        for q in area_queries(parsed_objective.get("geography", {})):
            area = await geocode(session, q)
            aid = overpass_area_id(area) if area else None
            if not aid:
                continue
            # one job per tag keeps each Overpass query small enough for a whole US state
            for tag in tags:
                jobs.append({"area": area_label(q), "area_id": aid, "tag": tag})
        return jobs

    async def execute(self, job: dict, integration, stats: dict) -> JobResult:
        k, v = job["tag"].split("=", 1)
        ql = f'[out:json][timeout:120];area({job["area_id"]})->.a;nwr["{k}"="{v}"](area.a);out center tags 5000;'
        async with httpx.AsyncClient(timeout=150, headers={"User-Agent": OSM_UA}) as c:
            r = await c.post(OVERPASS, content=ql)
            r.raise_for_status()
            data = r.json()
        hits: list[SourceHit] = []
        for el in data.get("elements", []):
            t = el.get("tags") or {}
            if not t.get("name"):
                continue
            center = el.get("center") or el
            street = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
            hits.append(SourceHit(
                source=self.key, external_id=f"{el['type']}/{el['id']}", name=t.get("name"),
                website_url=t.get("website") or t.get("contact:website") or t.get("url"),
                phone=t.get("phone") or t.get("contact:phone"), address=street or None, city=t.get("addr:city"), region=t.get("addr:state"),
                postal_code=t.get("addr:postcode"), country_code=(t.get("addr:country") or "")[:2].upper() or None,
                lat=center.get("lat"), lng=center.get("lon"), categories=[job["tag"]],
                raw={"tags": {kk: vv for kk, vv in t.items() if not kk.startswith("name:")}},
            ))
        return JobResult(hits=hits, note=f"{len(data.get('elements', []))} elements")

    async def test(self, integration) -> dict:
        # the public endpoint sheds load with 504/429; one retry keeps the probe honest without hammering it
        for attempt in range(2):
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": OSM_UA}) as c:
                r = await c.post(OVERPASS, content='[out:json][timeout:15];node(40.75,-73.99,40.76,-73.98)["amenity"="cafe"];out 1;')
            if r.status_code == 200 and "elements" in r.json():
                return {"ok": True, "message": "Overpass reachable"}
            if r.status_code not in (429, 504) or attempt:
                return {"ok": False, "message": f"HTTP {r.status_code}" + (" (public Overpass is overloaded; try again shortly)" if r.status_code in (429, 504) else "")}
            await asyncio.sleep(3)
        return {"ok": False, "message": "unreachable"}

    def describe(self, job: dict) -> str:
        return f"osm {job['tag']} in {job['area']}"
