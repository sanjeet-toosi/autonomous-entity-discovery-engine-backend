"""robots.txt handling with Redis cache (per registrable domain, 6h TTL)."""
from __future__ import annotations

import urllib.robotparser as rp

import httpx

from app.config import get_settings
from app.core.redis import get_redis

_TTL = 6 * 3600


async def is_allowed(client: httpx.AsyncClient, url: str, domain: str) -> bool:
    s = get_settings()
    if not s.crawl_respect_robots:
        return True
    redis = get_redis()
    key = f"bg:robots:{domain}"
    body = await redis.get(key)
    if body is None:
        try:
            r = await client.get(f"https://{domain}/robots.txt", timeout=8, follow_redirects=True)
            body = r.text if r.status_code == 200 and len(r.text) < 200_000 else ""
        except Exception:
            body = ""
        await redis.set(key, body, ex=_TTL)
    if not body:
        return True
    parser = rp.RobotFileParser()
    parser.parse(body.splitlines())
    return parser.can_fetch(s.crawl_user_agent, url) and parser.can_fetch("*", url) or parser.can_fetch(s.crawl_user_agent, url)
