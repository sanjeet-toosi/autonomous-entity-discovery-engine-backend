"""Network safety for the crawler: SSRF guard + blocklist.

Every outbound fetch resolves the host first and refuses private, loopback, link-local and
cloud-metadata addresses. Discovered URLs must never reach internal infrastructure."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.exceptions import CrawlBlocked

_BLOCKED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4",
        "240.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96",
    )
]
_ALLOWED_SCHEMES = {"http", "https"}


def is_ip_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return True
    return any(addr in net for net in _BLOCKED_NETWORKS)


async def resolve_and_validate(url: str) -> str:
    """Resolve the URL host and raise CrawlBlocked if any resolved address is not public."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise CrawlBlocked(f"scheme not allowed: {parts.scheme}")
    host = parts.hostname
    if not host or host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        raise CrawlBlocked(f"host not allowed: {host}")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise CrawlBlocked(f"dns_failure: {e}") from e
    if not infos:
        raise CrawlBlocked("dns_failure: no addresses")
    for info in infos:
        ip = info[4][0]
        if is_ip_blocked(ip):
            raise CrawlBlocked(f"resolved to non-public address {ip}")
    return host
