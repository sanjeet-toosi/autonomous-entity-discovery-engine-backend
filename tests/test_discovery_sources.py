"""Structured discovery sources: geo partitioning, applicability and deterministic job plans."""
import asyncio

from app.discovery.geo import area_queries, grid_cells, split_cell
from app.discovery.sources import get_source
from app.integrations.service import EffectiveIntegration
from app.integrations.catalog import SOURCE_CATALOG


def _integration(key, **cfg):
    return EffectiveIntegration(key=key, enabled=True, api_key="k", config=cfg, status={}, catalog=SOURCE_CATALOG[key])


def test_area_queries_prefer_named_cities_and_are_structured():
    assert area_queries({"country": "US", "regions": ["New York"]}) == [{"state": "New York", "country": "US"}]
    assert area_queries({"country": "US", "regions": ["New York"], "cities": ["Buffalo"]}) == [{"city": "Buffalo", "state": "New York", "country": "US"}]


def test_grid_cells_cover_bbox_and_split_is_quadtree():
    bbox = {"south": 40.0, "north": 41.0, "west": -74.0, "east": -73.0}
    cells = grid_cells(bbox, 30)
    assert len(cells) >= 12
    assert min(c["south"] for c in cells) == 40.0 and max(c["north"] for c in cells) == 41.0
    quads = split_cell(cells[0])
    assert len(quads) == 4 and all(q["north"] <= cells[0]["north"] and q["south"] >= cells[0]["south"] for q in quads)


def test_nppes_applies_only_to_us_healthcare():
    src = get_source("nppes")
    assert src.applies({"vertical": "home service", "geography": {"country": "US"}})[0] is False
    assert src.applies({"vertical": "dentist", "geography": {"country": "GB"}})[0] is False
    assert src.applies({"vertical": "dentist", "geography": {"country": "US"}})[0] is True


def test_nppes_plan_is_partitioned_by_state_city_and_taxonomy():
    src = get_source("nppes")
    po = {"vertical": "dentist", "geography": {"country": "US", "regions": ["New York"]}}
    jobs = asyncio.run(src.plan(None, po, {}, _integration("nppes")))
    assert jobs[0] == {"state": "NY", "region": "New York", "city": "", "taxonomy": "Dentist", "enumeration_type": "NPI-2", "skip": 0}
    assert {j["city"] for j in jobs} == {"", "New York City", "Buffalo", "Rochester"}
    with_people = asyncio.run(src.plan(None, po, {}, _integration("nppes", include_individuals=True)))
    assert len(with_people) == 2 * len(jobs)


def test_osm_applies_when_vertical_has_tags():
    src = get_source("osm_overpass")
    assert src.applies({"vertical": "home service"})[0] is True
    assert src.applies({"vertical": "saas"})[0] is False
