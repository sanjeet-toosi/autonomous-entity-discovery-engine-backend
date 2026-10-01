"""Search-engine discovery (DuckDuckGo via ddgs). Jobs are the strategy's query plan."""
from __future__ import annotations

import asyncio
import hashlib

from app.config import get_settings
from app.discovery.queries import build_query_plan
from app.discovery.search import search
from app.discovery.sources.base import JobResult, SourceHit


class WebSearchSource:
    key = "web_search"
    min_interval_seconds = 4

    def applies(self, parsed_objective: dict) -> tuple[bool, str]:
        return True, "applies to every mission"

    async def plan(self, session, parsed_objective, strategy_config, integration) -> list[dict]:
        return [{"query": q} for q in build_query_plan(parsed_objective, strategy_config)]

    async def execute(self, job: dict, integration, stats: dict) -> JobResult:
        s = get_settings()
        try:
            hits = await search(job["query"], s.discovery_max_results_per_query)
        except Exception as e:
            await asyncio.sleep(15)  # search engines rate-limit hard; back off before the next job
            raise
        out = [SourceHit(source=self.key, external_id=hashlib.sha1(h.url.encode()).hexdigest()[:40], name=h.title, website_url=h.url,
                         structured=False, snippet=h.snippet, raw={"title": h.title, "url": h.url}) for h in hits if h.url]
        return JobResult(hits=out)

    async def test(self, integration) -> dict:
        hits = await search("coffee shop new york", 3)
        return {"ok": len(hits) > 0, "message": f"{len(hits)} results for a probe query"}

    def describe(self, job: dict) -> str:
        return job["query"]
