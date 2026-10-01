from app.intelligence.sanitize import sanitize
from app.llm.interface import BusinessExtraction

MISSION = {"category_root": "Food & Beverage", "vertical": "restaurant"}


def _x(**kw):
    base = dict(is_business=True, entity_kind="business", category_path=[], matches_vertical=True, confidence=0.8)
    base.update(kw)
    return BusinessExtraction(**base)


def test_non_business_entity_kind_forces_not_business():
    x = sanitize(_x(entity_kind="government"), MISSION, "Alabama Travel", None)
    assert x.is_business is False and x.matches_vertical is False


def test_vertical_path_is_canonicalised():
    x = sanitize(_x(category_path=["food", "Restaurants", "Italian"]), MISSION, "T", None)
    assert x.category_path == ["Food & Beverage", "Restaurant", "Italian Restaurant"]
    x = sanitize(_x(category_path=[]), MISSION, "T", None)
    assert x.category_path == ["Food & Beverage", "Restaurant"]


def test_country_names_become_iso():
    assert sanitize(_x(country="United States"), MISSION, "T", None).country == "US"
    assert sanitize(_x(country="us"), MISSION, "T", None).country == "US"


def test_name_falls_back_to_title():
    assert sanitize(_x(name=None), MISSION, "Luigi's | Best Pasta in Town", None).name == "Luigi's"


def test_media_without_contact_is_not_business():
    x = sanitize(_x(name="Jersey Bites", description="A food blog about New Jersey restaurants"), MISSION, "Jersey Bites – NJ food blog", None, has_contact=False)
    assert x.is_business is False and x.entity_kind == "media"
    x = sanitize(_x(name="Jersey Bites", description="A food blog"), MISSION, "Jersey Bites", None, has_contact=True)
    assert x.is_business is True


def test_invented_entity_kinds_do_not_reject_a_business():
    from app.intelligence.sanitize import normalize_kind
    assert normalize_kind("contractor", True) == "business"
    assert normalize_kind("service provider", True) == "business"
    assert normalize_kind("hvacbusiness", True) == "business"
    assert normalize_kind("contractor", False) == "other"
    assert normalize_kind("news site", True) == "media"
    assert normalize_kind("online marketplace", True) == "aggregator"
    assert normalize_kind("directory", True) == "directory"
