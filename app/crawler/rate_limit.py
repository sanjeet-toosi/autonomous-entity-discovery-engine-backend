"""Per-domain token bucket in Redis (sliding window) + global concurrency semaphore."""
from __future__ import annotations

import asyncio
import time

from app.config import get_settings
from app.core.redis import get_redis

_global_sem: asyncio.Semaphore | None = None


def global_semaphore() -> asyncio.Semaphore:
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(get_settings().crawl_global_concurrency)
    return _global_sem


async def wait_for_domain_slot(domain: str) -> None:
    s = get_settings()
    redis = get_redis()
    key = f"bg:rl:{domain}"
    while True:
        now = time.time()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - 60)
            pipe.zcard(key)
            _, count = await pipe.execute()
        if count < s.crawl_per_domain_rpm:
            await redis.zadd(key, {str(now): now})
            await redis.expire(key, 120)
            return
        await asyncio.sleep(60 / s.crawl_per_domain_rpm)
