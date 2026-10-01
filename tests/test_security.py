import pytest

from app.core.exceptions import CrawlBlocked
from app.core.security import is_ip_blocked, resolve_and_validate


def test_private_ranges_blocked():
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5", "169.254.169.254", "::1", "fd00::1"):
        assert is_ip_blocked(ip)
    assert not is_ip_blocked("8.8.8.8")


@pytest.mark.asyncio
async def test_localhost_and_file_urls_rejected():
    with pytest.raises(CrawlBlocked):
        await resolve_and_validate("http://localhost/admin")
    with pytest.raises(CrawlBlocked):
        await resolve_and_validate("file:///etc/passwd")
    with pytest.raises(CrawlBlocked):
        await resolve_and_validate("http://127.0.0.1:8000/")
