"""
Static Maine town/village -> county lookup, restricted to the 5 target
counties (Sagadahoc, Lincoln, Knox, Waldo, Hancock).

Redfin's region-id resolution endpoints (location-autocomplete, the county
sitemap) are blocked by CloudFront from any source IP, so precise
county-scoped search via Redfin's own region system isn't available. Instead
we pull a broad lat/lon bounding-box of listings and filter to our 5 counties
by matching the listing's Redfin "city" field (which is frequently a postal
village name, not the legal town) against this table.

KNOWN LIMITATION: this table is manually maintained from public knowledge of
Maine's town/village names. It includes the legal municipalities plus the
common coastal postal villages, but is not guaranteed exhaustive -- an
obscure or newly-used village name could fail to match and cause a listing
to be silently excluded rather than flagged. See README "Known limitations".
"""

# Legal municipalities, by county.
_TOWNS = {
    "Sagadahoc": [
        "Arrowsic", "Bath", "Bowdoin", "Bowdoinham", "Georgetown",
        "Perkins Township", "Phippsburg", "Richmond", "Topsham", "West Bath",
        "Woolwich",
    ],
    "Lincoln": [
        "Alna", "Boothbay", "Boothbay Harbor", "Bremen", "Bristol",
        "Damariscotta", "Dresden", "Edgecomb", "Jefferson",
        "Monhegan", "Monhegan Plantation", "Newcastle", "Nobleboro",
        "South Bristol", "Southport", "Waldoboro", "Westport Island",
        "Whitefield",
    ],
    "Knox": [
        "Appleton", "Camden", "Cushing", "Friendship", "Hope",
        "Isle au Haut", "North Haven", "Owls Head", "Rockland", "Rockport",
        "South Thomaston", "St. George", "Saint George", "Thomaston",
        "Union", "Vinalhaven", "Warren", "Washington",
    ],
    "Waldo": [
        "Belfast", "Belmont", "Brooks", "Burnham", "Frankfort", "Freedom",
        "Islesboro", "Jackson", "Knox", "Liberty", "Lincolnville", "Monroe",
        "Montville", "Morrill", "Northport", "Palermo", "Prospect",
        "Searsmont", "Searsport", "Stockton Springs", "Swanville",
        "Thorndike", "Troy", "Unity", "Waldo", "Winterport",
    ],
    "Hancock": [
        "Amherst", "Aurora", "Bar Harbor", "Blue Hill", "Brooklin",
        "Brooksville", "Bucksport", "Castine", "Cranberry Isles", "Dedham",
        "Deer Isle", "Eastbrook", "Ellsworth", "Franklin", "Frenchboro",
        "Gouldsboro", "Great Pond", "Hancock", "Lamoine", "Mariaville",
        "Mount Desert", "Orland", "Osborn", "Otis", "Penobscot", "Sedgwick",
        "Sorrento", "Southwest Harbor", "Stonington", "Sullivan", "Surry",
        "Swans Island", "Tremont", "Trenton", "Verona Island", "Waltham",
        "Winter Harbor",
    ],
}

# Common coastal postal/village names that Redfin lists as "city" but are
# not the legal municipality name.
_VILLAGE_ALIASES = {
    # Sagadahoc
    "Small Point": "Sagadahoc",  # Phippsburg
    "Popham Beach": "Sagadahoc",  # Phippsburg
    "West Georgetown": "Sagadahoc",
    "Robinhood": "Sagadahoc",  # Georgetown
    # Lincoln
    "East Boothbay": "Lincoln",  # Boothbay
    "West Boothbay Harbor": "Lincoln",  # Boothbay
    "Ocean Point": "Lincoln",  # Boothbay
    "Christmas Cove": "Lincoln",  # South Bristol
    "Pemaquid": "Lincoln",  # Bristol
    "Round Pond": "Lincoln",  # Bristol
    "New Harbor": "Lincoln",  # Bristol
    "Chamberlain": "Lincoln",  # Bristol
    "Walpole": "Lincoln",  # Bristol/Damariscotta area
    "West Southport": "Lincoln",  # Southport
    # Knox
    "Spruce Head": "Knox",  # South Thomaston
    "Tenants Harbor": "Knox",  # St. George
    "Port Clyde": "Knox",  # St. George
    "Martinsville": "Knox",  # St. George
    "Glen Cove": "Knox",  # Rockport/Rockland area
    "Ash Point": "Knox",  # Owls Head
    # Waldo
    "Lincolnville Center": "Waldo",
    "Lincolnville Beach": "Waldo",
    "Temple Heights": "Waldo",
    # Hancock
    "Hulls Cove": "Hancock",  # Bar Harbor
    "Salsbury Cove": "Hancock",  # Bar Harbor
    "Bass Harbor": "Hancock",  # Tremont
    "Manset": "Hancock",  # Tremont
    "McKinley": "Hancock",  # Tremont
    "Northeast Harbor": "Hancock",  # Mount Desert
    "Seal Harbor": "Hancock",  # Mount Desert
    "Somesville": "Hancock",  # Mount Desert
    "Otter Creek": "Hancock",  # Mount Desert
    "Pretty Marsh": "Hancock",  # Mount Desert
    "Sunset": "Hancock",  # Deer Isle
    "Sunshine": "Hancock",  # Deer Isle
    "Little Deer Isle": "Hancock",  # Deer Isle/Sedgwick isthmus
    "Sargentville": "Hancock",  # Sedgwick
    "South Penobscot": "Hancock",  # Penobscot
    "Corea": "Hancock",  # Gouldsboro
    "Prospect Harbor": "Hancock",  # Gouldsboro
    "Birch Harbor": "Hancock",  # Gouldsboro
    "West Gouldsboro": "Hancock",
    "Hancock Point": "Hancock",
    "Sullivan Harbor": "Hancock",
}


def _build_index():
    index = {}
    for county, towns in _TOWNS.items():
        for t in towns:
            index[t.strip().lower()] = county
    for village, county in _VILLAGE_ALIASES.items():
        index[village.strip().lower()] = county
    return index


_INDEX = _build_index()


def county_for_city(city_name: str):
    """Return the target county for a Redfin 'city' string, or None if it
    doesn't match a known town/village in the target counties."""
    if not city_name:
        return None
    return _INDEX.get(city_name.strip().lower())


def is_target_county(city_name: str) -> bool:
    return county_for_city(city_name) is not None
