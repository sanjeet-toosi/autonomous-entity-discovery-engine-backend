"""Geography for structured sources: geocode a mission area (Nominatim, cached in PostgreSQL) and partition its
bounding box into cells that bounded APIs (Google Places) can exhaust one at a time."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GeoArea

NOMINATIM = "https://nominatim.openstreetmap.org/search"
GEO_UA = "BusinessGraphBot/0.1"


def area_queries(geography: dict) -> list[dict]:
    """Structured Nominatim queries for the mission area, most specific first. Structured (state=/city=) rather
    than free text, because "New York, US" free-text resolves to the city when the planner meant the state."""
    country = (geography or {}).get("country") or ""
    cities = (geography or {}).get("cities") or []
    regions = (geography or {}).get("regions") or []
    out: list[dict] = []
    for c in cities:
        out.append({k: v for k, v in (("city", c), ("state", regions[0] if regions else ""), ("country", country)) if v})
    if not cities:
        for r in regions:
            out.append({k: v for k, v in (("state", r), ("country", country)) if v})
    if not out and country:
        out.append({"country": country})
    seen: dict[str, dict] = {}
    for q in out:
        seen.setdefault(area_key(q), q)
    return list(seen.values())


def area_key(q: dict) -> str:
    return "|".join(f"{k}:{q[k]}" for k in ("city", "state", "country") if q.get(k))


def area_label(q: dict) -> str:
    return ", ".join(q[k] for k in ("city", "state", "country") if q.get(k))


async def geocode(session: AsyncSession, query: dict) -> GeoArea | None:
    key = area_key(query)
    cached = await session.get(GeoArea, key)
    if cached and cached.bbox:
        return cached
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": GEO_UA}) as c:
        r = await c.get(NOMINATIM, params={**query, "format": "jsonv2", "limit": 1})
        r.raise_for_status()
        rows = r.json()
    if not rows:
        return None
    row = rows[0]
    south, north, west, east = (float(x) for x in row["boundingbox"])
    area = cached or GeoArea(query=key)
    area.display_name = row.get("display_name")
    area.osm_type = row.get("osm_type")
    area.osm_id = int(row["osm_id"]) if row.get("osm_id") else None
    area.bbox = {"south": south, "north": north, "west": west, "east": east}
    area.lat = float(row["lat"])
    area.lng = float(row["lon"])
    area.fetched_at = datetime.now(timezone.utc)
    session.add(area)
    await session.flush()
    return area


def overpass_area_id(area: GeoArea) -> int | None:
    """Overpass area ids: relations are offset by 3.6e9, ways by 2.4e9."""
    if area.osm_id is None:
        return None
    if area.osm_type == "relation":
        return 3_600_000_000 + area.osm_id
    if area.osm_type == "way":
        return 2_400_000_000 + area.osm_id
    return None


def grid_cells(bbox: dict, cell_km: float) -> list[dict]:
    """Split a bbox into roughly square cells of `cell_km`. Returned as rectangles {south,north,west,east}."""
    south, north, west, east = bbox["south"], bbox["north"], bbox["west"], bbox["east"]
    lat_step = cell_km / 111.0
    mid_lat = (south + north) / 2
    lng_step = cell_km / (111.0 * max(0.2, math.cos(math.radians(mid_lat))))
    rows = max(1, math.ceil((north - south) / lat_step))
    cols = max(1, math.ceil((east - west) / lng_step))
    cells: list[dict] = []
    for i in range(rows):
        for j in range(cols):
            cells.append({"south": round(south + i * lat_step, 5), "north": round(min(north, south + (i + 1) * lat_step), 5),
                          "west": round(west + j * lng_step, 5), "east": round(min(east, west + (j + 1) * lng_step), 5)})
    return cells


def split_cell(cell: dict) -> list[dict]:
    """Quadtree split: a cell that returned the API's maximum probably holds more than it showed."""
    mid_lat = (cell["south"] + cell["north"]) / 2
    mid_lng = (cell["west"] + cell["east"]) / 2
    return [
        {"south": cell["south"], "north": mid_lat, "west": cell["west"], "east": mid_lng},
        {"south": cell["south"], "north": mid_lat, "west": mid_lng, "east": cell["east"]},
        {"south": mid_lat, "north": cell["north"], "west": cell["west"], "east": mid_lng},
        {"south": mid_lat, "north": cell["north"], "west": mid_lng, "east": cell["east"]},
    ]


def cell_label(cell: dict) -> str:
    return f"[{cell['south']:.3f},{cell['west']:.3f}→{cell['north']:.3f},{cell['east']:.3f}]"
