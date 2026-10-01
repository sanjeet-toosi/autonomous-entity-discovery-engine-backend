"""Worker entrypoint.

    python -m app.workers.run crawler --count 2 --concurrency 4
    python -m app.workers.run discovery
    python -m app.workers.run reflection
    python -m app.workers.run all          # one of each, for local development
"""
from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.core.database import dispose_engine
from app.core.logging import configure_logging
from app.core.redis import close_redis
from app.crawler.http import close_client
from app.graph.neo4j_store import get_neo4j
from app.workers.crawl_worker import CrawlWorker
from app.workers.discovery_worker import DiscoveryWorker
from app.workers.reflection_worker import ReflectionWorker


async def _main(kind: str, count: int, concurrency: int | None) -> None:
    workers = []
    if kind in ("crawler", "all"):
        workers += [CrawlWorker(i, concurrency) for i in range(count)]
    if kind in ("discovery", "all"):
        workers.append(DiscoveryWorker(0))
    if kind in ("reflection", "all"):
        workers.append(ReflectionWorker(0))
    try:
        await asyncio.gather(*(w.start() for w in workers))
    finally:
        await close_client()
        await get_neo4j().close()
        await close_redis()
        await dispose_engine()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["crawler", "discovery", "reflection", "all"])
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=None)
    a = p.parse_args()
    configure_logging(get_settings().log_level)
    asyncio.run(_main(a.kind, a.count, a.concurrency))


if __name__ == "__main__":
    main()
