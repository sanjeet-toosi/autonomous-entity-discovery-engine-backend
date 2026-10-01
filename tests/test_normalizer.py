from app.frontier.normalizer import normalize_host, registrable_domain, strip_tracking


def test_registrable_domain_strips_www_scheme_path_and_tracking():
    assert registrable_domain("https://www.Foo.com/about?utm_source=test") == "foo.com"
    assert registrable_domain("http://blog.example.co.uk/path") == "example.co.uk"
    assert registrable_domain("WWW.EXAMPLE.COM.") == "example.com"


def test_registrable_domain_rejects_ips_and_garbage():
    assert registrable_domain("127.0.0.1") is None
    assert registrable_domain("localhost") is None
    assert registrable_domain("") is None


def test_normalize_host_keeps_subdomain():
    assert normalize_host("https://www.menu.example.com/x") == "menu.example.com"


def test_strip_tracking():
    assert strip_tracking("https://a.com/p?utm_source=x&id=3#frag") == "https://a.com/p?id=3"
