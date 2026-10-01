"""Vertical vocabulary used by the heuristic planner/classifier and by discovery query generation."""
from __future__ import annotations

VERTICAL_HINTS: dict[str, dict] = {
    "restaurant": {
        "root": "Food & Beverage",
        "related": ["food service", "dining", "eatery"],
        "keywords": ["restaurant", "menu", "dine-in", "reservations", "takeout", "brunch", "dinner", "lunch",
                     "chef", "cuisine", "bistro", "grill", "kitchen", "order online", "happy hour"],
        "subcategories": {
            "Italian Restaurant": ["italian", "pizzeria", "trattoria", "pasta"],
            "Mexican Restaurant": ["mexican", "taqueria", "tacos", "burrito"],
            "Chinese Restaurant": ["chinese", "dim sum", "szechuan", "cantonese"],
            "Japanese Restaurant": ["japanese", "sushi", "ramen", "izakaya"],
            "Indian Restaurant": ["indian", "curry", "tandoori", "biryani"],
            "Thai Restaurant": ["thai", "pad thai"],
            "American Restaurant": ["american", "burgers", "diner", "steakhouse"],
            "Seafood Restaurant": ["seafood", "oyster", "crab", "lobster"],
            "Pizza Restaurant": ["pizza"],
            "Barbecue Restaurant": ["bbq", "barbecue", "smokehouse"],
            "Vegan Restaurant": ["vegan", "plant-based"],
            "Mediterranean Restaurant": ["mediterranean", "greek", "lebanese", "falafel"],
            "French Restaurant": ["french", "brasserie"],
            "Fast Food Restaurant": ["fast food", "drive-thru", "drive thru"],
        },
    },
    "cafe": {"root": "Food & Beverage", "related": ["coffee shop"], "keywords": ["cafe", "coffee", "espresso", "latte", "bakery"], "subcategories": {}},
    "bar": {"root": "Food & Beverage", "related": ["pub", "brewery"], "keywords": ["bar", "pub", "cocktails", "brewery", "taproom", "happy hour"], "subcategories": {}},
    "hotel": {"root": "Travel & Hospitality", "related": ["lodging", "resort"], "keywords": ["hotel", "rooms", "book now", "suites", "check-in", "resort", "inn"], "subcategories": {}},
    "dentist": {"root": "Healthcare", "related": ["dental clinic"], "keywords": ["dentist", "dental", "teeth", "orthodontic", "patients", "appointment"], "subcategories": {}},
    "clinic": {"root": "Healthcare", "related": ["medical practice"], "keywords": ["clinic", "patients", "physician", "medical", "appointment", "care"], "subcategories": {}},
    "hospital": {"root": "Healthcare", "related": [], "keywords": ["hospital", "emergency", "patients", "medical center"], "subcategories": {}},
    "gym": {"root": "Health & Fitness", "related": ["fitness center"], "keywords": ["gym", "fitness", "membership", "workout", "classes", "trainer"], "subcategories": {}},
    "law firm": {"root": "Professional Services", "related": ["attorney", "lawyer"], "keywords": ["attorney", "law firm", "lawyer", "legal", "practice areas", "consultation"], "subcategories": {}},
    "real estate agency": {"root": "Real Estate", "related": ["realtor"], "keywords": ["real estate", "realtor", "listings", "homes for sale", "properties"], "subcategories": {}},
    "saas": {"root": "Technology", "related": ["software"], "keywords": ["software", "platform", "pricing", "free trial", "api", "integrations", "sign up"], "subcategories": {}},
    "home service": {
        "root": "Home Services",
        "related": ["contractor", "home improvement", "home repair"],
        "keywords": ["plumbing", "plumber", "hvac", "heating", "air conditioning", "electrician", "electrical", "roofing", "roofer",
                     "cleaning service", "house cleaning", "landscaping", "lawn care", "handyman", "pest control", "painting",
                     "remodeling", "renovation", "contractor", "home repair", "free estimate", "licensed and insured", "emergency service"],
        "search_terms": ["plumber", "HVAC contractor", "electrician", "roofing contractor", "house cleaning service",
                         "landscaping company", "handyman service", "pest control company", "painting contractor", "home remodeling contractor"],
        "subcategories": {
            "Plumber": ["plumbing", "plumber", "drain", "water heater"],
            "HVAC Contractor": ["hvac", "heating", "air conditioning", "furnace", "ac repair"],
            "Electrician": ["electrician", "electrical"],
            "Roofing Contractor": ["roofing", "roofer", "roof repair"],
            "Cleaning Service": ["cleaning", "maid", "housekeeping", "janitorial"],
            "Landscaping Service": ["landscaping", "lawn care", "tree service", "lawn"],
            "Handyman": ["handyman"],
            "Pest Control Service": ["pest control", "exterminator"],
            "Painting Contractor": ["painting", "painter"],
            "Remodeling Contractor": ["remodeling", "renovation", "kitchen remodel", "bathroom remodel"],
        },
    },
    "plumber": {"root": "Home Services", "related": ["plumbing"], "keywords": ["plumbing", "plumber", "drain", "water heater", "emergency service"], "subcategories": {}},
    "salon": {"root": "Beauty & Personal Care", "related": ["hair salon", "spa"], "keywords": ["salon", "hair", "stylist", "spa", "nails", "book appointment"], "subcategories": {}},
    "school": {"root": "Education", "related": [], "keywords": ["school", "students", "admissions", "curriculum", "campus"], "subcategories": {}},
}

_ALIASES = {
    "restaurants": "restaurant", "eateries": "restaurant", "diners": "restaurant", "cafes": "cafe",
    "coffee shops": "cafe", "bars": "bar", "pubs": "bar", "hotels": "hotel", "dentists": "dentist",
    "dental clinics": "dentist", "clinics": "clinic", "hospitals": "hospital", "gyms": "gym",
    "law firms": "law firm", "lawyers": "law firm", "attorneys": "law firm", "realtors": "real estate agency",
    "real estate agencies": "real estate agency", "saas companies": "saas", "software companies": "saas",
    "plumbers": "plumber", "salons": "salon", "hair salons": "salon", "schools": "school",
    "home services": "home service", "home service companies": "home service", "home service providers": "home service",
    "home service businesses": "home service", "home improvement": "home service", "contractors": "home service",
}

# Structured-source vocabularies. OSM tags are "key=value" pairs; Google types are Places API (New) primary types;
# NPPES taxonomies are the registry's taxonomy_description search strings.
SOURCE_VOCAB: dict[str, dict] = {
    "restaurant": {"osm": ["amenity=restaurant", "amenity=fast_food"], "google": ["restaurant"]},
    "cafe": {"osm": ["amenity=cafe"], "google": ["cafe", "coffee_shop"]},
    "bar": {"osm": ["amenity=bar", "amenity=pub", "craft=brewery"], "google": ["bar", "pub"]},
    "hotel": {"osm": ["tourism=hotel", "tourism=motel", "tourism=guest_house"], "google": ["hotel", "motel", "lodging"]},
    "dentist": {"osm": ["amenity=dentist", "healthcare=dentist"], "google": ["dentist", "dental_clinic"], "nppes": ["Dentist"]},
    "clinic": {"osm": ["amenity=clinic", "amenity=doctors", "healthcare=clinic"], "google": ["doctor", "medical_clinic"], "nppes": ["Clinic/Center", "Physician"]},
    "hospital": {"osm": ["amenity=hospital"], "google": ["hospital"], "nppes": ["Hospital"]},
    "gym": {"osm": ["leisure=fitness_centre"], "google": ["gym", "fitness_center"]},
    "law firm": {"osm": ["office=lawyer"], "google": ["lawyer"]},
    "real estate agency": {"osm": ["office=estate_agent"], "google": ["real_estate_agency"]},
    "saas": {"osm": [], "google": []},
    "plumber": {"osm": ["craft=plumber"], "google": ["plumber"]},
    "salon": {"osm": ["shop=hairdresser", "shop=beauty"], "google": ["hair_salon", "beauty_salon"]},
    "school": {"osm": ["amenity=school"], "google": ["school"]},
    "home service": {
        "osm": ["craft=plumber", "craft=hvac", "craft=electrician", "craft=roofer", "craft=painter", "craft=cleaning", "craft=gardener",
                "craft=handyman", "craft=carpenter", "craft=tiler", "shop=hvac", "office=pest_control", "craft=pest_control"],
        "google": ["plumber", "electrician", "roofing_contractor", "painter", "general_contractor", "hvac_contractor"],
    },
}


def source_vocab(vertical: str, source: str) -> list[str]:
    return list(SOURCE_VOCAB.get(vertical, {}).get(source, []))


def search_terms(vertical: str) -> list[str]:
    """Concrete terms to put in search queries. Umbrella verticals (e.g. 'home service') expand into the trades
    people actually search for; everything else searches for the vertical itself."""
    terms = VERTICAL_HINTS.get(vertical, {}).get("search_terms") or []
    return list(dict.fromkeys([*terms, vertical])) if terms else [vertical]


def guess_vertical(text: str) -> tuple[str, str, list[str]]:
    for alias, vertical in sorted(_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in text:
            hints = VERTICAL_HINTS[vertical]
            return vertical, hints["root"], hints["keywords"]
    for vertical, hints in VERTICAL_HINTS.items():
        if vertical in text:
            return vertical, hints["root"], hints["keywords"]
    # Unknown vertical: take the noun after "all"/"find" as best effort
    import re

    m = re.search(r"(?:find|discover|all)\s+(?:all\s+)?([a-z][a-z \-]{2,40}?)(?:\s+in\b|\s+near\b|$)", text)
    vertical = (m.group(1).strip() if m else "business").rstrip("s") or "business"
    return vertical, "Business", [vertical]
