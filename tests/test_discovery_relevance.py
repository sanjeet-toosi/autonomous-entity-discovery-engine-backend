"""Discovery must stay on-mission: outbound links from off-topic pages are dropped, umbrella verticals
expand into concrete search terms, and the planner recognises 'home services'."""
from app.discovery.links import candidates_from_page
from app.discovery.queries import build_query_plan
from app.llm.taxonomy_hints import guess_vertical, search_terms

KWS = ["plumbing", "hvac", "roofing", "home repair"]


def test_outbound_links_from_non_directory_require_keyword_match():
    ext = {"vikings.com": "Minnesota Vikings", "jetadv.net": "Advertise with us", "acmeplumbingny.com": "Acme Plumbing", "roofingpros.com": ""}
    out = candidates_from_page(ext, referrer="buffalobills.com", vertical_keywords=KWS, source_quality=0.4, is_directory=False, max_domains=40)
    assert {c.domain for c in out} == {"acmeplumbingny.com", "roofingpros.com"}
    assert all(c.source_type == "outbound_link" for c in out)


def test_directory_pages_keep_all_outbound_domains():
    ext = {"joes-services.com": "Joe's", "smithbrothers.com": "Smith Brothers"}
    out = candidates_from_page(ext, referrer="nyhomepros.directory", vertical_keywords=KWS, source_quality=0.8, is_directory=True, max_domains=40)
    assert {c.domain for c in out} == set(ext)
    assert all(c.source_type == "directory_link" for c in out)


def test_keyword_gate_can_be_disabled_by_strategy():
    ext = {"random.com": "Random"}
    out = candidates_from_page(ext, referrer="x.com", vertical_keywords=KWS, source_quality=0.4, is_directory=False, max_domains=40, require_keyword_match=False)
    assert [c.domain for c in out] == ["random.com"]


def test_home_service_queries_expand_into_trades():
    plan = build_query_plan({"vertical": "home service", "geography": {"country": "US", "regions": ["New York"]}},
                            {"search_query_templates": ["{vertical} in {region}"]})
    assert "plumber in New York" in plan and "electrician in New York" in plan
    assert plan[-1] == "home service in New York"
    assert len(plan) == len(search_terms("home service"))


def test_plain_vertical_queries_unchanged():
    plan = build_query_plan({"vertical": "dentist", "geography": {"country": "US", "regions": ["Texas"]}}, {"search_query_templates": ["{vertical} in {region}"]})
    assert plan == ["dentist in Texas"]


def test_planner_recognises_home_services():
    vertical, root, keywords = guess_vertical("find all home services in the new york")
    assert vertical == "home service" and root == "Home Services" and "plumbing" in keywords
