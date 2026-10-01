"""Search discovery via DuckDuckGo (ddgs). Runs in a thread because the library is sync."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str


def _search_sync(query: str, max_results: int) -> list[SearchHit]:
    from ddgs import DDGS

    hits: list[SearchHit] = []
    with DDGS() as d:
        for r in d.text(query, max_results=max_results, region="us-en", safesearch="off"):
            url = r.get("href") or r.get("url") or ""
            if url:
                hits.append(SearchHit(url=url, title=r.get("title", ""), snippet=r.get("body", "")))
    return hits


async def search(query: str, max_results: int = 25) -> list[SearchHit]:
    return await asyncio.to_thread(_search_sync, query, max_results)
