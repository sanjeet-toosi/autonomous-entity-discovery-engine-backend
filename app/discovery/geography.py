"""Geographic expansion for discovery queries."""
from __future__ import annotations

US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming",
]

US_MAJOR_CITIES = {
    "New York": ["New York City", "Buffalo", "Rochester"], "California": ["Los Angeles", "San Francisco", "San Diego", "Sacramento", "San Jose"],
    "Texas": ["Houston", "Dallas", "Austin", "San Antonio"], "Florida": ["Miami", "Orlando", "Tampa", "Jacksonville"],
    "Illinois": ["Chicago"], "Pennsylvania": ["Philadelphia", "Pittsburgh"], "Arizona": ["Phoenix", "Tucson"], "Georgia": ["Atlanta"],
    "Washington": ["Seattle", "Spokane"], "Massachusetts": ["Boston"], "Colorado": ["Denver"], "Nevada": ["Las Vegas", "Reno"],
    "Oregon": ["Portland"], "Tennessee": ["Nashville", "Memphis"], "North Carolina": ["Charlotte", "Raleigh"], "Ohio": ["Columbus", "Cleveland", "Cincinnati"],
    "Michigan": ["Detroit"], "Minnesota": ["Minneapolis"], "Louisiana": ["New Orleans"], "Missouri": ["St. Louis", "Kansas City"],
    "Maryland": ["Baltimore"], "Wisconsin": ["Milwaukee"], "Utah": ["Salt Lake City"], "Indiana": ["Indianapolis"], "Oklahoma": ["Oklahoma City"],
}

US_STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}

COUNTRY_REGIONS = {"US": US_STATES}
COUNTRY_CITIES = {"US": US_MAJOR_CITIES}


def expansions(geography: dict) -> list[tuple[str, str]]:
    """Return (region, city) pairs to expand query templates; city may be ''."""
    country = (geography or {}).get("country")
    regions = (geography or {}).get("regions") or COUNTRY_REGIONS.get(country or "", [])
    cities_named = (geography or {}).get("cities") or []
    out: list[tuple[str, str]] = []
    if cities_named:
        for c in cities_named:
            out.append((regions[0] if regions else "", c))
    for r in regions:
        out.append((r, ""))
        for c in COUNTRY_CITIES.get(country or "", {}).get(r, [])[:3]:
            out.append((r, c))
    if not out:
        out.append((country or "", ""))
    return out
