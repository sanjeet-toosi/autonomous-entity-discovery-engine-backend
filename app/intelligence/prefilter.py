"""Cheap, deterministic rejections before any model call (saves cost, encodes semantic memory)."""
from __future__ import annotations

from app.crawler.parser import PageContent

_PARKED = ("domain is for sale", "buy this domain", "this domain may be for sale", "parked free", "domain parking",
           "is parked", "hugedomains", "godaddy.com/domains", "sedo.com", "afternic", "dan.com", "coming soon", "under construction")
_BUILDER_LANDING = ("create your website", "start your free trial", "website builder")


def prefilter(page: PageContent) -> tuple[str, str] | None:
    """Return (reject_status_reason, detail) if the page can be rejected without a model."""
    if page.status_code >= 400:
        return "http_error", f"status {page.status_code}"
    low = (page.text[:3000] + " " + (page.title or "")).lower()
    if len(page.text) < 40 and not page.schema_types:
        return "empty_page", "no textual content (possibly JS-only site)"
    if any(s in low for s in _PARKED):
        return "parked_domain", "parked / for-sale landing page"
    if any(s in low for s in _BUILDER_LANDING) and len(page.internal_links) < 3:
        return "builder_landing", "website builder landing page"
    return None
