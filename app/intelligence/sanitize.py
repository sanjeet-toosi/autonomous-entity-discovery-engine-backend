"""Post-model invariants. Models (especially small local ones) produce inconsistent outputs;
these rules make the graph well-formed regardless of which provider answered."""
from __future__ import annotations

import re

from app.llm.interface import BusinessExtraction

_NON_CATEGORY = {"business", "businesses", "government", "directory", "news", "other", "unknown", "none", "n/a", "company", "organization", "website"}
_ISO2 = re.compile(r"^[A-Z]{2}$")


def _norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).strip()
    return n[:-1] if n.endswith("s") and len(n) > 3 else n
_COUNTRY_NAMES = {"united states": "US", "usa": "US", "america": "US", "canada": "CA", "united kingdom": "GB", "uk": "GB", "australia": "AU", "india": "IN"}


_MEDIA_WORDS = ("magazine", "blog", "travel guide", "visitor guide", "tourism", "things to do", "best of", "top 10", "top ten", "news", "journal", "gazette", "podcast", "guide to")


_KNOWN_KINDS = {"business", "directory", "aggregator", "association", "media", "parked", "personal", "government", "other"}
_KIND_SYNONYMS = {
    "news": "media", "blog": "media", "magazine": "media", "publisher": "media", "press": "media",
    "marketplace": "aggregator", "review": "aggregator", "reviews": "aggregator", "review site": "aggregator", "platform": "aggregator",
    "portal": "directory", "listing": "directory", "listings": "directory", "yellow pages": "directory",
    "nonprofit": "association", "non-profit": "association", "charity": "association", "ngo": "association", "union": "association",
    "public": "government", "municipality": "government", "city": "government", "agency": "government", "school district": "government",
    "hosting": "parked", "landing": "parked", "builder": "parked", "for sale": "parked", "expired": "parked",
    "individual": "personal", "person": "personal", "portfolio": "personal", "resume": "personal",
}


def normalize_kind(raw: str | None, is_business: bool) -> str:
    """Small models invent entity kinds ('contractor', 'service provider', 'hvacbusiness'). Anything that is not a
    known non-business kind is a business when the model said so — otherwise a real plumber gets rejected as
    'contractor: contractor (0.95)'."""
    kind = (raw or "other").strip().lower()
    if kind in _KNOWN_KINDS:
        return kind
    for syn, canon in _KIND_SYNONYMS.items():
        if syn in kind:
            return canon
    return "business" if is_business else "other"


def sanitize(x: BusinessExtraction, mission: dict, page_title: str | None, og_site_name: str | None, *, has_contact: bool = True) -> BusinessExtraction:
    kind = normalize_kind(x.entity_kind, x.is_business)
    # Semantic memory: real businesses expose contact details; media/guide sites rarely do.
    if kind == "business" and not has_contact:
        blob = " ".join(filter(None, (page_title, og_site_name, x.name, x.description))).lower()
        if any(w in blob for w in _MEDIA_WORDS):
            kind = "media"
            x.evidence = [*x.evidence, "no contact details + media vocabulary → media, not business"]
    x.entity_kind = kind
    if kind != "business":
        x.is_business = False
        x.matches_vertical = False
    x.confidence = max(0.0, min(1.0, float(x.confidence or 0)))

    # category path -----------------------------------------------------------------
    root = (mission.get("category_root") or "Business").strip()
    vertical = (mission.get("vertical") or "business").strip()
    path: list[str] = []
    for raw in x.category_path or []:
        name = re.sub(r"\s+", " ", str(raw)).strip(" >›/").strip()
        if not name or name.lower() in _NON_CATEGORY:
            continue
        if name.lower() not in {p.lower() for p in path}:
            path.append(name)
    if x.is_business:
        if x.matches_vertical:
            vt = vertical.title()
            generic = {_norm(root), _norm(vt)} | {_norm(w) for w in re.split(r"[\s&/]+", root) if w}
            specifics = [p for p in path if _norm(p) not in generic]
            specific = specifics[-1] if specifics else None
            if specific and vertical.lower() not in specific.lower():
                specific = f"{specific} {vt}"
            path = [root, vt] + ([specific] if specific else [])
        elif not path:
            path = ["Uncategorized"]
    x.category_path = path[:3]

    # geography -----------------------------------------------------------------------
    c = (x.country or "").strip()
    if c and not _ISO2.match(c):
        c = _COUNTRY_NAMES.get(c.lower(), c[:2].upper() if len(c) == 2 else "")
    x.country = c.upper() if c else None
    x.region = (x.region or "").strip()[:100] or None
    x.city = (x.city or "").strip()[:100] or None

    # name ----------------------------------------------------------------------------
    if not x.name or len(x.name) < 2:
        cand = (og_site_name or page_title or "").split("|")[0].split(" - ")[0].split(" – ")[0].strip()
        x.name = cand[:120] or None
    x.name = x.name[:300] if x.name else None
    x.description = (x.description or "").strip()[:600] or None
    x.evidence = [str(e)[:200] for e in (x.evidence or [])[:8]]
    return x
