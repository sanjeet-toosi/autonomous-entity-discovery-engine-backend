"""Governed async HTTP fetcher: SSRF guard, blocklist, robots, per-domain + global rate limits,
timeouts, redirect and size limits. Returns a FetchResult or a classified FetchError."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.core.exceptions import CrawlBlocked
from app.core.security import resolve_and_validate
from app.crawler import rate_limit, robots
from app.frontier.normalizer import registrable_domain


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    html: str
    elapsed_ms: int
    redirected_domain: str | None  # set when the final registrable domain differs


class FetchError(Exception):
    def __init__(self, failure_type: str, detail: str, status_code: int | None = None):
        super().__init__(f"{failure_type}: {detail}")
        self.failure_type = failure_type
        self.detail = detail
        self.status_code = status_code


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = httpx.AsyncClient(
            headers={"User-Agent": s.crawl_user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                     "Accept-Language": "en-US,en;q=0.8"},
            timeout=httpx.Timeout(s.crawl_timeout_seconds, connect=8.0),
            follow_redirects=False,
            http2=True,
            limits=httpx.Limits(max_connections=s.crawl_global_concurrency + 8, max_keepalive_connections=32),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def classify_status(status: int) -> str | None:
    if status == 429:
        return "http_429"
    if status in (401, 403):
        return "http_403"
    if status == 404:
        return "http_404"
    if status >= 500:
        return "http_5xx"
    if status >= 400:
        return "http_4xx"
    return None


async def fetch(url: str, own_domain: str) -> FetchResult:
    s = get_settings()
    client = get_client()
    blocked = set(s.crawl_blocked_domains)
    t0 = time.perf_counter()
    current = url
    hops = 0
    async with rate_limit.global_semaphore():
        while True:
            reg = registrable_domain(current) or own_domain
            if reg in blocked:
                raise FetchError("blocked_domain", reg)
            try:
                await resolve_and_validate(current)
            except CrawlBlocked as e:
                raise FetchError("dns_failure" if "dns" in str(e) else "ssrf_blocked", str(e)) from e
            if hops == 0 and not await robots.is_allowed(client, current, reg):
                raise FetchError("robots_disallowed", current)
            await rate_limit.wait_for_domain_slot(reg)
            try:
                async with client.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        hops += 1
                        if hops > s.crawl_max_redirects:
                            raise FetchError("redirect_loop", current)
                        current = str(r.url.join(r.headers["location"]))
                        continue
                    ctype = r.headers.get("content-type", "")
                    if "html" not in ctype and "xml" not in ctype and ctype:
                        raise FetchError("non_html", ctype[:80], r.status_code)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > s.crawl_max_response_bytes:
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    enc = r.encoding or "utf-8"
                    try:
                        html = body.decode(enc, errors="replace")
                    except LookupError:
                        html = body.decode("utf-8", errors="replace")
                    final_reg = registrable_domain(str(r.url))
                    return FetchResult(
                        url=url, final_url=str(r.url), status_code=r.status_code, html=html,
                        elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        redirected_domain=final_reg if final_reg and final_reg != own_domain else None,
                    )
            except FetchError:
                raise
            except httpx.TimeoutException as e:
                raise FetchError("timeout", str(e)[:200]) from e
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
                raise FetchError("connection_error", str(e)[:200]) from e
            except httpx.HTTPError as e:
                raise FetchError("http_error", str(e)[:200]) from e
