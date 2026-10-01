"""Discovery worker: continuously expands the frontier from every *enabled* discovery source (web search,
OpenStreetMap, Google Places, registries...). Sources are round-robined per run; each keeps its own resumable
cursor in run.config.cursor_state["sources"], so pause/resume never repeats a query and the operator can
toggle sources in the UI mid-run. Structured listings are stored as source_records with provenance; those
with a website also enter the frontier so the crawler can verify and classify them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.brain import source_intelligence
from app.brain.strategies import active_config, ensure_seed_memories
from app.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.db.models import BusinessNode, Mission, Run, SourceRecord
from app.discovery.sources import SOURCE_ORDER, JobResult, get_source
from app.events.publisher import broadcast, emit
from app.events.types import Category, Event
from app.frontier import service as frontier
from app.frontier.normalizer import registrable_domain
from app.frontier.service import CandidateInput
from app.integrations import service as integrations
from app.llm.taxonomy_hints import search_terms
from app.metrics import service as metrics
from app.workers.base import BaseWorker

log = get_logger("discovery-worker")
MAX_SOURCE_ERRORS = 5


class DiscoveryWorker(BaseWorker):
    worker_type = "discovery"

    async def run(self) -> None:
        s = get_settings()
        async with session_scope() as sess:
            await ensure_seed_memories(sess)
        while not self.stopping.is_set():
            did_work = False
            try:
                did_work = await self._tick()
            except Exception:
                log.exception("discovery tick failed")
                self.failed += 1
                await self.sleep(5)
            await self.sleep(s.discovery_search_delay_seconds if did_work else 5)

    async def _tick(self) -> bool:
        s = get_settings()
        async with session_scope() as sess:
            runs = await self.claimable_runs(sess)
            if not runs:
                self.current_job = None
                return False
            eff = await integrations.effective(sess)
            for run in runs:
                counts = await frontier.counts(sess, mission_id=run.mission_id)
                if counts["queued_total"] >= s.discovery_queue_backpressure:
                    self.current_job = {"run_id": str(run.id), "stage": "backpressure", "queued": counts["queued_total"]}
                    continue
                mission = await sess.get(Mission, run.mission_id)
                po = mission.parsed_objective
                cfg, _ = await active_config(sess, po.get("vertical", "business"))
                cursor = dict(run.config.get("cursor_state", {}))
                states: dict[str, dict] = {k: dict(v) for k, v in cursor.get("sources", {}).items()}
                if "search_index" in cursor and "web_search" not in states:  # cursor written before sources existed
                    states["web_search"] = {"index": int(cursor["search_index"])}
                active = [k for k in SOURCE_ORDER if eff[k].usable and get_source(k).applies(po)[0]]
                if not active:
                    self.current_job = {"run_id": str(run.id), "stage": "no_sources"}
                    continue
                rr = int(cursor.get("rr", 0)) % len(active)
                picked = None
                for i in range(len(active)):
                    key = active[(rr + i) % len(active)]
                    st = states.setdefault(key, {})
                    if st.get("done"):
                        continue
                    if st.get("next_at") and datetime.fromisoformat(st["next_at"]) > datetime.now(timezone.utc):
                        continue  # pacing / backoff window for this source
                    src = get_source(key)
                    plan = await src.plan(sess, po, cfg, eff[key])
                    idx = int(st.get("index", 0))
                    extra = list(st.get("extra", []))
                    if idx < len(plan):
                        job, st["index"] = plan[idx], idx + 1
                    elif extra:
                        job, st["extra"] = extra[0], extra[1:]
                    else:
                        st["done"] = True
                        continue
                    picked = (key, src, job, st, len(plan))
                    cursor["rr"] = (active.index(key) + 1) % len(active)
                    break
                if picked is None:
                    self.current_job = {"run_id": str(run.id), "stage": "plan_exhausted", "sources": active}
                    run.config = {**run.config, "cursor_state": {**cursor, "sources": states}}
                    continue
                key, src, job, st, plan_len = picked
                label = src.describe(job)
                self.current_job = {"run_id": str(run.id), "stage": "search", "source": key, "query": label, "index": st.get("index", 0), "of": plan_len}
                await self.heartbeat()
                try:
                    result = await src.execute(job, eff[key], st)
                    st["errors"] = 0
                    st["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=src.min_interval_seconds)).isoformat()
                except Exception as e:
                    st["errors"] = int(st.get("errors", 0)) + 1
                    rate_limited = "429" in str(e) or "504" in str(e)
                    st["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=90 if rate_limited else 20)).isoformat()
                    if rate_limited:  # the job did not run: put it back at the front so it is retried after the backoff
                        st["extra"] = [job, *st.get("extra", [])]
                    await emit(sess, Event.WORKER_ERROR, f"{key}: '{label}' failed: {str(e)[:160]}", category=Category.ERRORS, level="warning",
                               run_id=run.id, mission_id=mission.id, worker_id=self.worker_id, payload={"source": key})
                    if st["errors"] >= MAX_SOURCE_ERRORS:
                        st["done"] = True
                        st["stopped_reason"] = f"{MAX_SOURCE_ERRORS} consecutive failures"
                        await emit(sess, Event.WORKER_ERROR, f"{key}: stopped for this run after {MAX_SOURCE_ERRORS} consecutive failures",
                                   category=Category.ERRORS, level="error", run_id=run.id, mission_id=mission.id, worker_id=self.worker_id, payload={"source": key})
                    result = None
                if result is not None:
                    st["extra"] = [*st.get("extra", []), *result.follow_up_jobs]
                    st["queries"] = int(st.get("queries", 0)) + 1
                    st["hits"] = int(st.get("hits", 0)) + len(result.hits)
                    added = await self._ingest(sess, run, mission, key, label, result, cfg)
                    st["added"] = int(st.get("added", 0)) + added
                cursor["sources"] = states
                cursor["last_query_at"] = datetime.now(timezone.utc).isoformat()
                run.config = {**run.config, "cursor_state": cursor, "seeded": True}
                self.completed += 1
                return True
        return False

    async def _ingest(self, sess, run: Run, mission: Mission, key: str, label: str, result: JobResult, cfg: dict) -> int:
        po = mission.parsed_objective
        vertical = po.get("vertical", "business")
        kws = [k.lower() for k in [*po.get("keywords", []), vertical, *po.get("related_verticals", []), *search_terms(vertical)] if k]
        learned = await source_intelligence.quality_for(sess, key, label, vertical, cfg.get("source_weights", {}).get(key, 0.85 if key != "web_search" else 0.8))
        cands: list[CandidateInput] = []
        rows: list[dict] = []
        no_site = 0
        for h in result.hits:
            if h.structured:
                d = registrable_domain(h.website_url) if h.website_url else None
                rows.append({"mission_id": mission.id, "run_id": run.id, "source": key, "external_id": h.external_id[:200], "name": (h.name or "")[:300] or None,
                             "website_domain": d, "website_url": (h.website_url or "")[:1000] or None, "phone": (h.phone or "")[:40] or None,
                             "address": (h.address or "")[:500] or None, "city": (h.city or "")[:100] or None, "region": (h.region or "")[:100] or None,
                             "postal_code": (h.postal_code or "")[:20] or None, "country_code": h.country_code, "lat": h.lat, "lng": h.lng,
                             "categories": h.categories[:10], "raw": h.raw, "last_seen_at": datetime.now(timezone.utc)})
                if not d:
                    no_site += 1
                    continue
                listing = {"source": key, "external_id": h.external_id, "name": h.name, "phone": h.phone, "address": h.address, "city": h.city, "region": h.region,
                           "postal_code": h.postal_code, "country_code": h.country_code, "lat": h.lat, "lng": h.lng, "categories": h.categories[:5]}
                cands.append(CandidateInput(domain=d, source_type=key, source_reference=label[:1000], mission_relevance=0.85, business_probability=0.8,
                                            source_quality=learned, novelty=0.6, information_gain=0.5, metadata={"listing": listing}))
            else:
                d = registrable_domain(h.website_url or "")
                if not d:
                    continue
                text = f"{h.name or ''} {h.snippet}".lower()
                hits_n = sum(1 for k in kws if k in text)
                directoryish = any(w in text for w in ("best ", "top ", "near you", "list of", "directory", "guide to"))
                cands.append(CandidateInput(
                    domain=d, source_type="search", source_reference=label, mission_relevance=min(0.95, 0.5 + 0.1 * hits_n) if hits_n else 0.35,
                    business_probability=0.35 if directoryish else 0.6, source_quality=learned, novelty=0.7, information_gain=0.7 if directoryish else 0.5,
                    metadata={"title": (h.name or "")[:120], "url": (h.website_url or "")[:300]},
                ))
        if rows:
            stmt = pg_insert(SourceRecord).values(rows)
            stmt = stmt.on_conflict_do_update(constraint="uq_source_record", set_={"run_id": stmt.excluded.run_id, "last_seen_at": stmt.excluded.last_seen_at,
                                                                                   "website_domain": stmt.excluded.website_domain, "phone": stmt.excluded.phone,
                                                                                   "address": stmt.excluded.address, "raw": stmt.excluded.raw})
            await sess.execute(stmt)
            # resolve listings onto businesses we already know by domain
            await sess.execute(update(SourceRecord).where(SourceRecord.run_id == run.id, SourceRecord.matched_business_id.is_(None),
                                                          SourceRecord.website_domain.isnot(None),
                                                          SourceRecord.website_domain == BusinessNode.domain).values(matched_business_id=BusinessNode.id))
        r = await frontier.add_candidates(sess, mission.id, run.id, cands, priority_weights=cfg.get("priority_weights"))
        await metrics.incr_many(sess, {metrics.SEARCHES_EXECUTED: 1, metrics.CANDIDATES_ADDED: r.added, metrics.DUPLICATES_PREVENTED: r.duplicates},
                                run_id=run.id, mission_id=mission.id)
        await source_intelligence.observe_candidates(sess, key, label, vertical, seen=len(cands), novel=r.added, duplicates=r.duplicates)
        msg = f"{get_source(key).__class__.__name__.replace('Source', '')} '{label}' → {len(result.hits)} results, {r.added} new candidates, {r.duplicates} duplicates"
        if no_site:
            msg += f", {no_site} listings without website kept as records"
        if result.note:
            msg += f" ({result.note})"
        await emit(sess, Event.SEARCH_EXECUTED, msg, category=Category.DISCOVERY, run_id=run.id, mission_id=mission.id, worker_id=self.worker_id,
                   payload={"source": key, "query": label, "results": len(result.hits), "added": r.added, "duplicates": r.duplicates,
                            "records": len(rows), "without_website": no_site, "sample": r.added_domains[:6]})
        await broadcast(Event.FRONTIER_UPDATED, {"added": r.added, "duplicates": r.duplicates, "source": key}, run_id=run.id, category=Category.DISCOVERY)
        return r.added
