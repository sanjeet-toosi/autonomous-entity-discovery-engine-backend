from app.crawler.parser import parse_html

HTML = """<html lang="en"><head><title>Luigi's Trattoria | Italian Restaurant in Austin</title>
<meta name="description" content="Family owned Italian restaurant serving fresh pasta since 1982.">
<meta property="og:site_name" content="Luigi's Trattoria">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Restaurant","name":"Luigi's","telephone":"(512) 555-0100","address":{"@type":"PostalAddress","addressLocality":"Austin","addressRegion":"TX","postalCode":"78701"}}</script>
</head><body><nav><a href="/menu">Menu</a><a href="/reservations">Reserve a table</a><a href="/locations">Locations</a></nav>
<p>Visit us at 123 Main St, Austin, TX 78701. Call (512) 555-0100.</p>
<a href="https://www.instagram.com/luigis">Instagram</a><a href="https://partnerfarm.com">Our farm partner</a></body></html>"""


def test_parse_extracts_signals():
    p = parse_html("https://luigis.com/", "https://luigis.com/", 200, HTML, "luigis.com")
    assert p.title.startswith("Luigi's Trattoria")
    assert p.og_site_name == "Luigi's Trattoria"
    assert "Restaurant" in p.schema_types
    assert p.has_phone and p.has_address and p.has_menu_link and p.has_reservation_link and p.has_locations_link
    assert p.city_hint == "Austin"
    assert "/menu" in p.internal_links
    assert "instagram.com" in p.external_domains and "partnerfarm.com" in p.external_domains
    assert p.language == "en"
