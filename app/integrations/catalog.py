"""Catalog of discovery sources the operator can switch on/off from the UI.

Free sources default on. Paid APIs need a key and default off; registries default off because they only
apply to some verticals/countries. The worker consults the effective state on every tick, so toggling in
the UI takes effect without restarting anything."""
from __future__ import annotations

SOURCE_CATALOG: dict[str, dict] = {
    "web_search": {
        "label": "Web search (DuckDuckGo)",
        "kind": "free",
        "requires_key": False,
        "default_enabled": True,
        "description": "Generated search queries (vertical × geography × templates). Finds business websites and directories.",
        "docs_url": None,
        "config_fields": [],
    },
    "osm_overpass": {
        "label": "OpenStreetMap (Overpass)",
        "kind": "free",
        "requires_key": False,
        "default_enabled": True,
        "description": "Structured map data: businesses tagged with the vertical's OSM tags inside the mission area, with address, phone and website.",
        "docs_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
        "config_fields": [],
    },
    "google_places": {
        "label": "Google Places API",
        "kind": "paid_api",
        "requires_key": True,
        "default_enabled": False,
        "description": "Text Search (New) over a grid of cells covering the mission area; cells that hit the 60-result cap are split. Billed per request by Google.",
        "docs_url": "https://developers.google.com/maps/documentation/places/web-service/text-search",
        "config_fields": [
            {"key": "cell_km", "label": "Initial cell size (km)", "type": "number", "default": 25},
            {"key": "max_requests_per_run", "label": "Request budget per run", "type": "number", "default": 500},
        ],
    },
    "nppes": {
        "label": "NPPES NPI Registry (US healthcare)",
        "kind": "registry",
        "requires_key": False,
        "default_enabled": False,
        "description": "CMS provider registry. Applies to US healthcare verticals (dentist, clinic, hospital...). Returns organisations with practice address and phone; no websites.",
        "docs_url": "https://npiregistry.cms.hhs.gov/api-page",
        "config_fields": [
            {"key": "include_individuals", "label": "Include individual providers (NPI-1)", "type": "boolean", "default": False},
        ],
    },
}
