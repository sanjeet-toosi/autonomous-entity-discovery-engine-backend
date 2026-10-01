from app.llm.interface import MissionPlan
from app.llm.providers.heuristic import plan_mission
from app.missions.parser import normalize_plan


def test_plan_restaurants_us():
    p = plan_mission("Find all restaurants in the US")
    assert p.vertical == "restaurant" and p.geography.country == "US" and p.category_root == "Food & Beverage"
    assert "menu" in p.keywords


def test_plan_dentists_in_texas_sets_region():
    p = plan_mission("find every dentist in texas")
    assert p.vertical == "dentist" and p.geography.country == "US" and "Texas" in p.geography.regions


def test_normalize_weak_model_output():
    p = normalize_plan(MissionPlan(vertical="restaurants", category_root="food", summary="Find all restaurants in the US", geography={"country": "us"}), "Find all restaurants in the US")
    assert p.vertical == "restaurant" and p.category_root == "Food & Beverage" and p.geography.country == "US"
