"""Single centralized domain normalization service (Public Suffix List aware)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import tldextract

_extract = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|source$)")


def registrable_domain(url_or_host: str) -> str | None:
    """https://www.Foo.com/about?utm=x -> foo.com ; returns None for IPs / invalid."""
    host = url_or_host.strip().lower()
    if "://" in host or host.startswith("//"):
        host = urlsplit(host if "://" in host else "http:" + host).hostname or ""
    host = host.split("/")[0].split("?")[0].split("#")[0].rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or re.fullmatch(r"[\d.]+", host) or ":" in host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        return None
    ext = _extract(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def normalize_host(url_or_host: str) -> str | None:
    """Full host without www (keeps meaningful subdomains); used as the business `domain`."""
    host = url_or_host.strip().lower()
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.split("/")[0].rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def homepage_url(domain: str) -> str:
    return f"https://{domain}/"


def strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    keep = "&".join(kv for kv in parts.query.split("&") if not _TRACKING.match(kv.split("=")[0]))
    return parts._replace(query=keep, fragment="").geturl()
