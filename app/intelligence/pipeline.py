"""The per-domain processing pipeline (Explorer's controlled tool sequence):

  claim → crawl homepage → prefilter → follow key paths → understand (extract+classify)
  → entity resolution → graph write → link discovery → experience

Every stage emits activity events, and the whole outcome becomes an experience record."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain import experiences, failures, source_intelligence
from app.crawler.http import FetchError, classify_status, fetch
from app.crawler.parser import PageContent, parse_html
from app.db.models import BusinessNode, CandidateDomain, Mission, Page, Run, SourceRecord
from app.discovery.links import candidates_from_page
from app.events.publisher import broadcast, emit
from app.events.types import Category, Event
from app.frontier import service as frontier
from app.frontier.normalizer import homepage_url
from app.graph import writer
from app.intelligence.prefilter import prefilter
from app.intelligence.sanitize import sanitize
from app.llm.router import get_router
from app.metrics import service as metrics
from app.missions.parser import mission_context


@dataclass
class PipelineContext:
    session: AsyncSession
    run: Run
    mission: Mission
    candidate: CandidateDomain
    worker_id: str
    strategy_config: dict
    strategy_label: str
    actions: list[str] = field(default_factory=list)
    requests: int = 0
    pages: list[PageContent] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    @property
    def vertical(self) -> str:
        return self.mission.parsed_objective.get("vertical", "business")

    async def stage(self, stage: str, **payload) -> None:
        await broadcast(Event.STAGE_CHANGED, {"stage": stage, "domain": self.candidate.normalized_domain, "worker_id": self.worker_id, **payload},
                        run_id=self.run.id, category=Category.WORKERS, worker_id=self.worker_id, domain=self.candidate.normalized_domain)


async def process_candidate(ctx: PipelineContext) -> dict:
    s, run, cand = ctx.session, ctx.run, ctx.candidate
    domain = cand.normalized_domain
    mid = ctx.mission.id
    await emit(s, Event.DOMAIN_CRAWL_STARTED, f"Fetching homepage of {domain}", category=Category.CRAWL, run_id=run.id, mission_id=mid,
               worker_id=ctx.worker_id, domain=domain, payload={"source": cand.source_type, "priority": cand.priority, "attempt": cand.attempt_count})
    await ctx.stage("crawl")

    # 1. crawl homepage --------------------------------------------------------------
    try:
        res = await fetch(homepage_url(domain), domain)
        ctx.requests += 1
        ctx.actions.append("fetch_homepage")
    except FetchError as e:
        return await _handle_fetch_failure(ctx, e)
    status_failure = classify_status(res.status_code)
    if status_failure:
        return await _handle_fetch_failure(ctx, FetchError(status_failure, f"HTTP {res.status_code}", res.status_code), final_url=res.final_url)

    canonical = res.redirected_domain or domain
    page = parse_html(res.url, res.final_url, res.status_code, res.html, canonical)
    ctx.pages.append(page)
    await metrics.incr(s, metrics.PAGES_FETCHED, run_id=run.id, mission_id=mid)
    await _store_page(ctx, page, res.elapsed_ms)
    await emit(s, Event.DOMAIN_CRAWLED, f"Fetched {page.final_url} ({res.status_code}, {len(page.text)} chars"
               + (f", schema.org {', '.join(page.schema_types[:3])}" if page.schema_types else "") + ")",
               category=Category.CRAWL, run_id=run.id, mission_id=mid, worker_id=ctx.worker_id, domain=domain,
               payload={"status_code": res.status_code, "schema_types": page.schema_types[:5], "ms": res.elapsed_ms, "redirected_to": res.redirected_domain})

    # 2. prefilter -------------------------------------------------------------------
    rejected = prefilter(page)
    if rejected:
        reason, detail = rejected
        ctx.actions.append("prefilter_reject")
        await failures.record(s, reason, domain, detail, page.final_url, run.id)
        # A page the prefilter throws out (parked, builder landing, social, ...) says nothing about the mission;
        # its outbound links are not discovery signal.
        return await _reject(ctx, reason, detail, discovered_links={})

    # 3. follow key internal paths (strategy-driven) -----------------------------------
    await _follow_key_paths(ctx, page, canonical)

    # 4. understand: business detection + extraction + classification (one model task) --
    await ctx.stage("classification")
    merged = _merge_pages(ctx.pages)
    listing = (cand.metadata_ or {}).get("listing")  # structured-source evidence (Google Places / OSM / registry)
    page_summary = merged.summary_for_model()
    if listing:
        page_summary["listing"] = listing
    extraction, provider = await get_router().extract_business(page_summary, mission_context(ctx.mission.parsed_objective),
                                                              run_id=run.id, domain=domain)
    extraction = sanitize(extraction, mission_context(ctx.mission.parsed_objective), merged.title, merged.og_site_name,
                          has_contact=merged.has_phone or merged.has_address or bool(listing))
    if listing:
        _apply_listing(extraction, listing)
    ctx.actions.append(f"understand:{provider}")
    await emit(s, Event.CLASSIFICATION_COMPLETE,
               (f"Identified {extraction.name or domain} as {' › '.join(extraction.category_path) or extraction.entity_kind} — {extraction.confidence:.0%}"
                if extraction.is_business else f"Not a business: {extraction.entity_kind} ({extraction.confidence:.0%})"),
               category=Category.AI, run_id=run.id, mission_id=mid, worker_id=ctx.worker_id, domain=domain,
               payload={"is_business": extraction.is_business, "entity_kind": extraction.entity_kind, "category_path": extraction.category_path,
                        "confidence": extraction.confidence, "provider": provider, "matches_vertical": extraction.matches_vertical,
                        "country": extraction.country, "region": extraction.region})

    min_conf = float(ctx.strategy_config.get("min_business_confidence", 0.35))
    if not extraction.is_business or extraction.confidence < min_conf:
        reason = extraction.entity_kind if not extraction.is_business else "low_confidence"
        # Only a directory *of the mission vertical* is a discovery source. Links from a rejected news site,
        # sports team or government page lead to more of the same, not to businesses the operator asked for.
        is_dir = extraction.is_directory_of_vertical
        return await _reject(ctx, reason, f"{extraction.entity_kind} ({extraction.confidence:.2f})",
                             discovered_links=merged.external_domains if is_dir else {}, is_directory=is_dir, source_quality=0.8 if is_dir else 0.4)

    # 5. entity resolution ------------------------------------------------------------
    await ctx.stage("entity_resolution")
    business_domain = canonical
    if res.redirected_domain:
        existing = await s.scalar(select(BusinessNode).where(BusinessNode.domain == res.redirected_domain))
        ctx.actions.append("resolve_redirect_alias")
        await emit(s, Event.BUSINESS_DETECTED, f"{domain} redirects to {res.redirected_domain} — treating as alias of {'existing' if existing else 'new'} business",
                   category=Category.AI, run_id=run.id, mission_id=mid, worker_id=ctx.worker_id, domain=domain, payload={"canonical": res.redirected_domain})

    # 6. graph write -------------------------------------------------------------------
    await ctx.stage("graph_update")
    evidence_items = [(f"model:{provider} evidence", ev, extraction.confidence) for ev in extraction.evidence[:6]]
    if merged.schema_types:
        evidence_items.append(("schema.org types", ", ".join(merged.schema_types[:6]), 0.95))
    if merged.description:
        evidence_items.append(("meta description", merged.description, 0.6))
    if merged.has_address:
        evidence_items.append(("html text address/zip pattern", "postal address detected", 0.7))
    if listing:
        evidence_items.append((f"{listing['source']} listing", " — ".join(str(x) for x in (listing.get("name"), listing.get("address"), listing.get("phone")) if x)[:500], 0.9))
    result = await writer.write_business(
        s, domain=business_domain, registrable=canonical, extraction=extraction, page_url=page.final_url, evidence_items=evidence_items,
        run_id=run.id, mission_id=mid, source_type=cand.source_type, source_reference=cand.source_reference, worker_id=ctx.worker_id,
        metadata={"aliases": [domain] if domain != business_domain else [], "matches_vertical": extraction.matches_vertical,
                  "provider": provider, "pages_inspected": len(ctx.pages), "schema_types": merged.schema_types[:8],
                  **({"listing": listing} if listing else {})},
    )
    ctx.actions.append("graph_write_create" if result.created else "graph_write_update")
    # entity resolution against structured sources: every listing with this website now points at the business
    await s.execute(update(SourceRecord).where(SourceRecord.website_domain.in_({domain, business_domain}), SourceRecord.matched_business_id.is_(None))
                    .values(matched_business_id=result.business.id))
    await metrics.incr(s, metrics.DOMAINS_CRAWLED, run_id=run.id, mission_id=mid)

    # 7. link discovery ----------------------------------------------------------------
    await ctx.stage("discovery")
    new_candidates, dupes = await _discover(ctx, merged.external_domains, is_directory=False, source_quality=0.5)
    linked = await writer.link_businesses(s, result.business, list(merged.external_domains)[:100], run.id)
    if linked:
        await emit(s, Event.EDGE_CREATED, f"Linked {domain} → {linked} known business(es)", category=Category.GRAPH, run_id=run.id, mission_id=mid,
                   worker_id=ctx.worker_id, domain=domain, payload={"edges": linked})

    # 8. finish ------------------------------------------------------------------------
    await frontier.complete(s, cand.id, "PROCESSED", None, {"business_id": str(result.business.id), "confidence": extraction.confidence})
    await _experience(ctx, {"valid_business": True, "created": result.created, "new_nodes": int(result.created), "new_candidates": new_candidates,
                            "duplicates": dupes, "confidence": extraction.confidence, "matches_vertical": extraction.matches_vertical,
                            "category_path": extraction.category_path, "provider": provider}, valid=True, failed=False, downstream=new_candidates)
    return {"status": "PROCESSED", "business_id": str(result.business.id), "created": result.created, "new_candidates": new_candidates}


# ---------------------------------------------------------------------------------------
def _apply_listing(extraction, listing: dict) -> None:
    """A structured listing (map/registry) is strong, independent evidence: fill geography the page did not state
    and keep a confirmed business from being thrown out on a shy model score."""
    extraction.country = extraction.country or listing.get("country_code")
    extraction.region = extraction.region or listing.get("region")
    extraction.city = extraction.city or listing.get("city")
    if extraction.is_business:
        extraction.confidence = max(extraction.confidence, 0.6)
    if not extraction.name and listing.get("name"):
        extraction.name = listing["name"]


async def _follow_key_paths(ctx: PipelineContext, home: PageContent, canonical: str) -> None:
    cfg = ctx.strategy_config
    max_pages = int(cfg.get("max_pages_per_domain", 3))
    wanted = [p.lower() for p in cfg.get("follow_paths", [])]
    kws = [k.lower() for k in cfg.get("follow_path_keywords", [])]
    chosen: list[str] = []
    for path in home.internal_links:
        low = path.lower().rstrip("/")
        if low in wanted or any(k in low for k in kws):
            if path not in chosen:
                chosen.append(path)
        if len(chosen) >= max_pages - 1:
            break
    for path in chosen:
        url = f"https://{canonical}{path}" if path.startswith("/") else f"https://{canonical}/{path}"
        try:
            r = await fetch(url, canonical)
            ctx.requests += 1
            if r.status_code < 400:
                p = parse_html(r.url, r.final_url, r.status_code, r.html, canonical)
                ctx.pages.append(p)
                ctx.actions.append(f"fetch_path:{path.split('/')[1][:20] if '/' in path else path[:20]}")
                await _store_page(ctx, p, r.elapsed_ms)
                await metrics.incr(ctx.session, metrics.PAGES_FETCHED, run_id=ctx.run.id, mission_id=ctx.mission.id)
        except FetchError as e:
            ctx.actions.append(f"fetch_path_failed:{e.failure_type}")
        await frontier.extend_lease(ctx.session, ctx.candidate.id)


def _merge_pages(pages: list[PageContent]) -> PageContent:
    if len(pages) == 1:
        return pages[0]
    home = pages[0]
    merged = PageContent(url=home.url, final_url=home.final_url, status_code=home.status_code, title=home.title, description=home.description,
                         og_site_name=home.og_site_name, language=home.language, city_hint=home.city_hint)
    seen_types: list[str] = []
    ext: dict[str, str] = {}
    texts = []
    for p in pages:
        for t in p.schema_types:
            if t not in seen_types:
                seen_types.append(t)
        merged.json_ld.extend(p.json_ld)
        ext.update(p.external_domains)
        texts.append(p.text[:2500])
        merged.has_phone |= p.has_phone
        merged.has_address |= p.has_address
        merged.has_menu_link |= p.has_menu_link
        merged.has_reservation_link |= p.has_reservation_link
        merged.has_locations_link |= p.has_locations_link
        merged.city_hint = merged.city_hint or p.city_hint
        merged.description = merged.description or p.description
    merged.schema_types = seen_types
    merged.external_domains = ext
    merged.internal_links = home.internal_links
    merged.text = " || ".join(texts)
    return merged


async def _store_page(ctx: PipelineContext, page: PageContent, ms: int) -> None:
    ctx.session.add(Page(
        candidate_id=ctx.candidate.id, run_id=ctx.run.id, domain=ctx.candidate.normalized_domain, url=page.url, final_url=page.final_url,
        status_code=page.status_code, title=page.title, content_hash=page.content_hash,
        structured_data={"schema_types": page.schema_types, "json_ld": page.json_ld[:5], "description": page.description, "language": page.language},
        clean_text=page.text[:6000], links={"internal": page.internal_links[:60], "external": list(page.external_domains)[:100], "external_count": len(page.external_domains)},
        crawl_metadata={"worker_id": ctx.worker_id, "has_phone": page.has_phone, "has_address": page.has_address}, fetch_ms=ms,
    ))


async def _discover(ctx: PipelineContext, external: dict[str, str], *, is_directory: bool, source_quality: float) -> tuple[int, int]:
    if not external:
        return 0, 0
    cfg = ctx.strategy_config
    learned_quality = await source_intelligence.quality_for(ctx.session, "directory_link" if is_directory else "outbound_link",
                                                            ctx.candidate.normalized_domain, ctx.vertical, source_quality)
    cands = candidates_from_page(external, referrer=ctx.candidate.normalized_domain, vertical_keywords=_relevance_keywords(ctx),
                                 source_quality=learned_quality, is_directory=is_directory, max_domains=int(cfg.get("link_discovery_max_domains", 40)),
                                 require_keyword_match=bool(cfg.get("outbound_require_keyword_match", True)))
    if not cands:
        return 0, 0
    r = await frontier.add_candidates(ctx.session, ctx.mission.id, ctx.run.id, cands, priority_weights=cfg.get("priority_weights"))
    ctx.actions.append("discover_links")
    await metrics.incr_many(ctx.session, {metrics.CANDIDATES_ADDED: r.added, metrics.DUPLICATES_PREVENTED: r.duplicates}, run_id=ctx.run.id, mission_id=ctx.mission.id)
    await source_intelligence.observe_candidates(ctx.session, "directory_link" if is_directory else "outbound_link", ctx.candidate.normalized_domain,
                                                 ctx.vertical, seen=len(cands), novel=r.added, duplicates=r.duplicates)
    if r.added or r.duplicates:
        await emit(ctx.session, Event.DOMAIN_DISCOVERED, f"Discovered {len(cands)} candidate domains on {ctx.candidate.normalized_domain}: "
                   f"{r.added} new added to frontier, {r.duplicates} duplicates skipped", category=Category.DISCOVERY, run_id=ctx.run.id,
                   mission_id=ctx.mission.id, worker_id=ctx.worker_id, domain=ctx.candidate.normalized_domain,
                   payload={"added": r.added, "duplicates": r.duplicates, "sample": r.added_domains[:8], "is_directory": is_directory})
        await broadcast(Event.FRONTIER_UPDATED, {"added": r.added, "duplicates": r.duplicates}, run_id=ctx.run.id, category=Category.DISCOVERY)
    return r.added, r.duplicates


def _relevance_keywords(ctx: PipelineContext) -> list[str]:
    """Mission vocabulary for scoring outbound links: strategy keywords ∪ mission keywords ∪ vertical names."""
    parsed = ctx.mission.parsed_objective
    seen: dict[str, None] = {}
    for k in [*ctx.strategy_config.get("outbound_relevance_keywords", []), *parsed.get("keywords", []),
              parsed.get("vertical", ""), *parsed.get("related_verticals", [])]:
        k = (k or "").strip().lower()
        if len(k) >= 3:
            seen[k] = None
    return list(seen)


async def _reject(ctx: PipelineContext, reason: str, detail: str, *, discovered_links: dict[str, str], is_directory: bool = False, source_quality: float = 0.4) -> dict:
    s, run, cand = ctx.session, ctx.run, ctx.candidate
    new_c = dupes = 0
    if discovered_links:
        new_c, dupes = await _discover(ctx, discovered_links, is_directory=is_directory, source_quality=source_quality)
    await frontier.complete(s, cand.id, "REJECTED", f"{reason}: {detail}")
    await metrics.incr_many(s, {metrics.DOMAINS_REJECTED: 1, metrics.DOMAINS_CRAWLED: 1}, run_id=run.id, mission_id=ctx.mission.id)
    await emit(s, Event.DOMAIN_REJECTED, f"Rejected {cand.normalized_domain}: {reason} ({detail})" + (f" — but harvested {new_c} candidates" if new_c else ""),
               category=Category.CRAWL, run_id=run.id, mission_id=ctx.mission.id, worker_id=ctx.worker_id, domain=cand.normalized_domain,
               payload={"reason": reason, "detail": detail, "new_candidates": new_c, "is_directory": is_directory})
    await _experience(ctx, {"valid_business": False, "reject_reason": reason, "new_candidates": new_c, "duplicates": dupes, "is_directory": is_directory},
                      valid=False, failed=False, downstream=new_c)
    return {"status": "REJECTED", "reason": reason, "new_candidates": new_c}


async def _handle_fetch_failure(ctx: PipelineContext, e: FetchError, final_url: str | None = None) -> dict:
    s, run, cand = ctx.session, ctx.run, ctx.candidate
    domain = cand.normalized_domain
    ctx.actions.append(f"fetch_failed:{e.failure_type}")
    await failures.record(s, e.failure_type, domain, e.detail, final_url or homepage_url(domain), run.id)
    status = await frontier.fail_or_retry(s, cand, e.failure_type, e.detail)
    await metrics.incr(s, metrics.CRAWL_ERRORS, run_id=run.id, mission_id=ctx.mission.id)
    if status == "RETRY":
        await emit(s, Event.DOMAIN_RETRY, f"{domain}: {e.failure_type} ({e.detail[:80]}) — will retry", category=Category.ERRORS, level="warning",
                   run_id=run.id, mission_id=ctx.mission.id, worker_id=ctx.worker_id, domain=domain, payload={"failure_type": e.failure_type, "attempt": cand.attempt_count})
    else:
        await metrics.incr(s, metrics.DOMAINS_CRAWLED, run_id=run.id, mission_id=ctx.mission.id)
        await emit(s, Event.DOMAIN_FAILED, f"{domain}: {e.failure_type} ({e.detail[:80]})", category=Category.ERRORS, level="error",
                   run_id=run.id, mission_id=ctx.mission.id, worker_id=ctx.worker_id, domain=domain, payload={"failure_type": e.failure_type})
    await _experience(ctx, {"valid_business": False, "failure_type": e.failure_type, "status": status}, valid=False, failed=True, downstream=0)
    return {"status": status, "failure_type": e.failure_type}


async def _experience(ctx: PipelineContext, result: dict, *, valid: bool, failed: bool, downstream: int) -> None:
    duration = int((time.perf_counter() - ctx.started) * 1000)
    await experiences.record(ctx.session, mission_id=ctx.mission.id, run_id=ctx.run.id, domain=ctx.candidate.normalized_domain, vertical=ctx.vertical,
                             source_type=ctx.candidate.source_type, source_reference=ctx.candidate.source_reference, strategy_version=ctx.strategy_label,
                             actions=ctx.actions, result=result, cost={"requests": ctx.requests, "pages": len(ctx.pages)}, duration_ms=duration)
    await source_intelligence.observe_outcome(ctx.session, ctx.candidate.source_type, ctx.candidate.source_reference, ctx.vertical, valid=valid, failed=failed, downstream=downstream)
