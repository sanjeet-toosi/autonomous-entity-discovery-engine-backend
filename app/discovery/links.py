"""Recursive discovery: turn a crawled page's outbound domains into frontier candidates,
scoring mission relevance from anchor text / URL keywords."""
from __future__ import annotations

from app.frontier.service import CandidateInput

_NOISE_ANCHORS = {"privacy", "terms", "login", "sign in", "cookie", "powered by", "site by", "website by", "designed by"}


def candidates_from_page(external_domains: dict[str, str], *, referrer: str, vertical_keywords: list[str], source_quality: float,
                         is_directory: bool, max_domains: int, require_keyword_match: bool = True) -> list[CandidateInput]:
    """Directories of the vertical are trusted: every outbound domain is a candidate. Any other page only
    contributes domains whose name or anchor text matches the mission vocabulary — otherwise a rejected news
    or sports site would flood the frontier with its unrelated partners."""
    kws = [k.lower() for k in vertical_keywords if k]
    out: list[CandidateInput] = []
    for domain, anchor in external_domains.items():
        a = (anchor or "").lower()
        if any(n in a for n in _NOISE_ANCHORS):
            continue
        text = f"{domain} {a}"
        hits = sum(1 for k in kws if k in text)
        if not hits and not is_directory and require_keyword_match and kws:
            continue
        relevance = min(0.95, 0.35 + 0.2 * hits) if hits else (0.55 if is_directory else 0.3)
        out.append(CandidateInput(
            domain=domain, source_type="directory_link" if is_directory else "outbound_link", source_reference=referrer,
            mission_relevance=relevance, business_probability=0.6 if is_directory else 0.45, source_quality=source_quality,
            novelty=0.6, information_gain=0.5 if is_directory else 0.4, metadata={"anchor": anchor[:80]} if anchor else {},
        ))
    out.sort(key=lambda c: -c.mission_relevance)
    return out[:max_domains]
