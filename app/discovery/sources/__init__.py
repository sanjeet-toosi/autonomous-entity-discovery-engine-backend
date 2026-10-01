"""Registry of discovery sources, keyed like the integrations catalog."""
from __future__ import annotations

from app.discovery.sources.base import DiscoverySource, JobResult, SourceHit
from app.discovery.sources.google_places import GooglePlacesSource
from app.discovery.sources.nppes import NppesSource
from app.discovery.sources.osm_overpass import OsmOverpassSource
from app.discovery.sources.web_search import WebSearchSource

_SOURCES: dict[str, DiscoverySource] = {s.key: s for s in (WebSearchSource(), OsmOverpassSource(), GooglePlacesSource(), NppesSource())}
SOURCE_ORDER = list(_SOURCES)


def get_source(key: str) -> DiscoverySource:
    return _SOURCES[key]


__all__ = ["DiscoverySource", "JobResult", "SourceHit", "get_source", "SOURCE_ORDER"]
