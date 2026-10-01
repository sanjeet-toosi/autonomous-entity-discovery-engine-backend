"""HTML -> PageContent: title, meta, OpenGraph, JSON-LD types, clean text, internal/external links
and cheap business signals. Uses selectolax for speed."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from app.frontier.normalizer import registrable_domain

_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_ADDRESS = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.\- ]{2,40}\s(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|hwy|pkwy)\b", re.I)
_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
_WS = re.compile(r"\s+")


@dataclass
class PageContent:
    url: str
    final_url: str
    status_code: int
    title: str | None = None
    description: str | None = None
    og_site_name: str | None = None
    language: str | None = None
    schema_types: list[str] = field(default_factory=list)
    json_ld: list[dict] = field(default_factory=list)
    text: str = ""
    content_hash: str = ""
    internal_links: list[str] = field(default_factory=list)
    external_domains: dict[str, str] = field(default_factory=dict)  # domain -> anchor text sample
    has_phone: bool = False
    has_address: bool = False
    has_menu_link: bool = False
    has_reservation_link: bool = False
    has_locations_link: bool = False
    city_hint: str | None = None

    def summary_for_model(self, max_chars: int = 3500) -> dict:
        return {
            "url": self.final_url,
            "status_code": self.status_code,
            "title": self.title,
            "description": self.description,
            "og_site_name": self.og_site_name,
            "language": self.language,
            "schema_types": self.schema_types[:8],
            "json_ld_sample": _compact_ld(self.json_ld)[:3],
            "text_excerpt": self.text[:max_chars],
            "has_phone": self.has_phone,
            "has_address": self.has_address,
            "has_menu_link": self.has_menu_link,
            "has_reservation_link": self.has_reservation_link,
            "has_locations_link": self.has_locations_link,
            "internal_link_sample": self.internal_links[:15],
            "external_domain_count": len(self.external_domains),
            "external_domain_sample": list(self.external_domains)[:15],
            "city_hint": self.city_hint,
        }


def _compact_ld(items: list[dict]) -> list[dict]:
    keep = ("@type", "name", "description", "address", "telephone", "servesCuisine", "priceRange", "areaServed")
    out = []
    for it in items:
        out.append({k: (v if not isinstance(v, (dict, list)) else json.dumps(v)[:300]) for k, v in it.items() if k in keep})
    return out


def parse_html(url: str, final_url: str, status_code: int, html: str, own_domain: str) -> PageContent:
    tree = HTMLParser(html)
    page = PageContent(url=url, final_url=final_url, status_code=status_code)
    html_node = tree.css_first("html")
    page.language = (html_node.attributes.get("lang") if html_node else None) or None

    t = tree.css_first("title")
    page.title = _WS.sub(" ", t.text()).strip()[:500] if t else None
    for m in tree.css("meta"):
        a = m.attributes
        name = (a.get("name") or a.get("property") or "").lower()
        content = (a.get("content") or "").strip()
        if not content:
            continue
        if name in ("description", "og:description") and not page.description:
            page.description = content[:1000]
        elif name == "og:site_name":
            page.og_site_name = content[:200]
        elif name == "og:title" and not page.title:
            page.title = content[:500]

    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text() or "")
        except Exception:
            continue
        for item in _flatten_ld(data):
            page.json_ld.append(item)
            types = item.get("@type")
            for ty in types if isinstance(types, list) else [types]:
                if isinstance(ty, str) and ty not in page.schema_types:
                    page.schema_types.append(ty)
            addr = item.get("address")
            if isinstance(addr, dict) and addr.get("addressLocality") and not page.city_hint:
                page.city_hint = str(addr["addressLocality"])[:100]
            if item.get("telephone"):
                page.has_phone = True

    for node in tree.css("script, style, noscript, svg, iframe"):
        node.decompose()
    body = tree.css_first("body") or tree.root
    text = _WS.sub(" ", body.text(separator=" ")).strip() if body else ""
    page.text = text[:20000]
    page.content_hash = hashlib.sha256(page.text.encode()).hexdigest()

    if _PHONE.search(text):
        page.has_phone = True
    if _ADDRESS.search(text) or _ZIP.search(text):
        page.has_address = True

    base_host = urlsplit(final_url).hostname or ""
    seen_internal: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(final_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        host = (parts.hostname or "").lower()
        anchor = _WS.sub(" ", a.text()).strip()[:80]
        low = (parts.path + " " + anchor).lower()
        if "menu" in low:
            page.has_menu_link = True
        if any(k in low for k in ("reserv", "book a table", "opentable", "resy")):
            page.has_reservation_link = True
        if "location" in low:
            page.has_locations_link = True
        reg = registrable_domain(host) if host else None
        if host == base_host or (reg and reg == own_domain):
            path = parts.path or "/"
            if path not in seen_internal and len(seen_internal) < 200:
                seen_internal.add(path)
                page.internal_links.append(path)
        elif reg and reg != own_domain and reg not in page.external_domains and len(page.external_domains) < 300:
            page.external_domains[reg] = anchor
    return page


def _flatten_ld(data) -> list[dict]:
    out: list[dict] = []
    if isinstance(data, list):
        for d in data:
            out.extend(_flatten_ld(d))
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            out.extend(_flatten_ld(data["@graph"]))
        else:
            out.append(data)
    return out
