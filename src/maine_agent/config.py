"""Central configuration and tunable thresholds."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DB_PATH = REPO_ROOT / "data" / "state.sqlite3"

# Target counties (midcoast + Down East Maine).
TARGET_COUNTIES = ["Sagadahoc", "Lincoln", "Knox", "Waldo", "Hancock"]

# Per-county bounding boxes (lon0, lat0, lon1, lat1), used to query Redfin's
# gis-csv endpoint. Redfin's region-id lookup endpoints (location-
# autocomplete, county sitemaps) are blocked by CloudFront regardless of
# source IP, so we search by geography instead and filter precisely to the
# target counties ourselves (see towns.py). A single bounding box over all 5
# counties hits Redfin's 350-row response cap and silently truncates results
# (dominated by inland Kennebec/Penobscot county inventory), so each county
# gets its own tight box sized to its real footprint, including offshore
# islands. Boxes deliberately overlap neighbors' edges; town-based county
# attribution (towns.py) resolves ambiguity and results are de-duplicated by
# MLS#/URL after fetching.
COUNTY_SEARCH_BOXES = {
    "Sagadahoc": (-70.05, 43.70, -69.60, 44.15),
    "Lincoln": (-69.85, 43.70, -69.30, 44.25),
    "Knox": (-69.35, 43.90, -68.60, 44.35),
    "Waldo": (-69.35, 44.15, -68.75, 44.70),
    "Hancock": (-68.85, 44.05, -67.95, 44.70),
}

# Price bands (USD).
PRICE_MAIN_MAX = 2_000_000
PRICE_UPPER_MIN = 2_000_000
PRICE_UPPER_MAX = 4_000_000

# Sailboat assumptions.
DRAFT_FT = 5.5
UNDER_KEEL_MARGIN_FT = 2.0
DATUM_MARGIN_FT = 1.5
MIN_DEPTH_FT_MLLW = 9.0  # draft + clearance + margin, rounded per spec

SHORELINE_SEARCH_RADIUS_M = 150
MOORING_SEARCH_RADIUS_M = 1000
BRIDGE_SEARCH_RADIUS_M = 800
MAST_HEIGHT_FT = 50

# Depth sampling.
SAMPLE_BEARINGS_DEG = list(range(0, 360, 30))  # 12 directions
SAMPLE_STEP_M = 15
SAMPLE_MAX_STEPS = SHORELINE_SEARCH_RADIUS_M // SAMPLE_STEP_M

# Fallback NAVD88->MLLW offset (meters) if the VDatum API is unreachable.
# Approximate midcoast-Maine average; real offset varies ~1.2-2.5m across
# the search area and is fetched per-listing from NOAA VDatum when possible.
FALLBACK_NAVD88_TO_MLLW_OFFSET_M = 1.7

# Each newly-seen listing costs one Overpass call plus, if it's oceanfront,
# up to ~55 NOAA DEM calls (5 bearings x 11 samples) and a VDatum call --
# observed in practice at roughly 10-20s/listing against the shared public
# Overpass instance under load. A single run against the full ~1700-listing
# candidate universe could take multiple hours, risking an Actions job
# timeout and delaying the very first report by most of a day. Capping new
# assessments per run keeps each run's duration bounded and predictable; a
# large initial backlog is naturally cleared over the first several daily
# runs instead of one marathon run, since already-assessed listings are
# cached and skipped on every subsequent run regardless of this cap.
MAX_NEW_ASSESSMENTS_PER_RUN = 250

USER_AGENT = "MaineOceanfrontListingAgent/0.1 (contact: varnerj08@gmail.com)"
HTTP_TIMEOUT_S = 20

NOMINATIM_CONTACT_EMAIL = "varnerj08@gmail.com"
