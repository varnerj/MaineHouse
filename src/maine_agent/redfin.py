"""
Redfin unofficial gis-csv fetcher.

Redfin's location-autocomplete and county-sitemap endpoints (needed to
resolve a human county name to Redfin's internal region_id) are blocked at
the CloudFront edge for every source IP we tested, including GitHub Actions
runners. The stingray/api/gis-csv endpoint, queried with an explicit lat/lon
bounding polygon instead of a region_id, is NOT blocked and returns live MLS
data. This module always queries by polygon and lets callers/towns.py filter
to the target counties afterward.
"""
import csv
import io
import logging
from dataclasses import dataclass
from typing import Optional

from . import config, towns
from .httpclient import get

log = logging.getLogger(__name__)

GIS_CSV_URL = "https://www.redfin.com/stingray/api/gis-csv"
WARMUP_URL = "https://www.redfin.com/state/Maine"


@dataclass
class RawListing:
    address: str
    city: str
    state: str
    zip: str
    price: Optional[int]
    beds: Optional[float]
    baths: Optional[float]
    sqft: Optional[int]
    lot_size_sqft: Optional[int]
    year_built: Optional[int]
    days_on_market: Optional[int]
    status: str
    url: str
    mls_number: str
    source: str
    lat: Optional[float]
    lon: Optional[float]
    property_type: str
    county: str  # resolved from towns.py

    @property
    def listing_id(self) -> str:
        # MLS# is the stable Redfin/MLS identifier; fall back to URL.
        return self.mls_number or self.url


def _to_int(v):
    v = (v or "").replace(",", "").strip()
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _to_float(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_csv(text: str):
    lines = text.splitlines()
    # Redfin prepends an MLS-rules disclaimer line before the real header in
    # some responses; find the header row (starts with "SALE TYPE").
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("SALE TYPE"):
            start = i
            break
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return list(reader)


def _box_to_poly(box):
    lon0, lat0, lon1, lat1 = box
    return f"{lon0} {lat0},{lon1} {lat0},{lon1} {lat1},{lon0} {lat1},{lon0} {lat0}"


def _split_box(box):
    lon0, lat0, lon1, lat1 = box
    lon_mid = (lon0 + lon1) / 2
    lat_mid = (lat0 + lat1) / 2
    return [
        (lon0, lat0, lon_mid, lat_mid),
        (lon_mid, lat0, lon1, lat_mid),
        (lon0, lat_mid, lon_mid, lat1),
        (lon_mid, lat_mid, lon1, lat1),
    ]


CAP_THRESHOLD = 350  # Redfin gis-csv silently truncates at num_homes=350
MAX_SPLIT_DEPTH = 6  # 4^6 = 4096 leaf boxes worst case; converges much sooner in practice


def fetch_raw_rows_for_box(poly, status="9", uipt="1,5", sf="1,2,3,5,6,7", num_homes=350):
    """Fetch listings within a single lon/lat bounding polygon. status=9 ->
    active-for-sale listings; uipt=1,5 -> single-family + vacant land per
    spec; sf are structure filters Redfin applies by default on its own UI
    for a similar search."""
    params = {
        "al": 1,
        "poly": poly,
        "market": "maine",
        "num_homes": num_homes,
        "ord": "redfin-recommended-asc",
        "page_number": 1,
        "sf": sf,
        "status": status,
        "uipt": uipt,
        "v": 8,
    }
    headers = {"Referer": WARMUP_URL}
    status_code, body = get(GIS_CSV_URL, params=params, headers=headers)
    return _parse_csv(body)


def _fetch_box_recursive(box, depth=0):
    """Fetch a box, splitting into 4 sub-boxes and recursing whenever the
    result looks truncated by Redfin's ~350-row cap, so we never silently
    drop listings that fall off the end of a capped response."""
    poly = _box_to_poly(box)
    rows = fetch_raw_rows_for_box(poly)
    if len(rows) < CAP_THRESHOLD:
        return rows
    if depth >= MAX_SPLIT_DEPTH:
        log.error(
            "Redfin result still capped at max split depth (%d) for box %s "
            "(%d rows returned). Some listings in this box are being "
            "silently dropped by Redfin's response cap -- narrow "
            "COUNTY_SEARCH_BOXES or raise MAX_SPLIT_DEPTH.",
            depth, box, len(rows),
        )
        return rows
    log.info("Box %s hit the row cap (%d rows); splitting into 4 sub-boxes", box, len(rows))
    merged = []
    seen = set()
    for sub_box in _split_box(box):
        for r in _fetch_box_recursive(sub_box, depth=depth + 1):
            key = r.get("MLS#") or r.get(
                "URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)"
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
    return merged


def fetch_raw_rows(county_boxes=config.COUNTY_SEARCH_BOXES):
    """Fetch and merge raw rows across all per-county bounding boxes,
    de-duplicating by MLS# (falling back to listing URL), adaptively
    splitting any box that hits Redfin's row cap."""
    # Warm up cookies against a normal page first; harmless if it errors
    # (seen occasionally on some runners) since gis-csv works without a
    # valid session either way.
    try:
        get(WARMUP_URL)
    except Exception as e:  # noqa: BLE001
        log.info("warmup request failed (continuing anyway): %s", e)

    seen_keys = set()
    merged = []
    for county, box in county_boxes.items():
        rows = _fetch_box_recursive(box)
        log.info("Redfin gis-csv for %s returned %d rows total (after any splitting)", county, len(rows))
        for r in rows:
            key = r.get("MLS#") or r.get(
                "URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)"
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(r)
    log.info("Redfin gis-csv merged total: %d unique rows across %d county boxes", len(merged), len(county_boxes))
    return merged


def to_raw_listings(rows, target_counties=config.TARGET_COUNTIES):
    out = []
    for r in rows:
        city = r.get("CITY", "")
        county = towns.county_for_city(city)
        if county is None or county not in target_counties:
            continue
        url = r.get("URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)", "")
        out.append(
            RawListing(
                address=r.get("ADDRESS", "").strip(),
                city=city,
                state=r.get("STATE OR PROVINCE", ""),
                zip=r.get("ZIP OR POSTAL CODE", ""),
                price=_to_int(r.get("PRICE")),
                beds=_to_float(r.get("BEDS")),
                baths=_to_float(r.get("BATHS")),
                sqft=_to_int(r.get("SQUARE FEET")),
                lot_size_sqft=_to_int(r.get("LOT SIZE")),
                year_built=_to_int(r.get("YEAR BUILT")),
                days_on_market=_to_int(r.get("DAYS ON MARKET")),
                status=r.get("STATUS", ""),
                url=url if url.startswith("http") else f"https://www.redfin.com{url}",
                mls_number=r.get("MLS#", ""),
                source="redfin",
                lat=_to_float(r.get("LATITUDE")),
                lon=_to_float(r.get("LONGITUDE")),
                property_type=r.get("PROPERTY TYPE", ""),
                county=county,
            )
        )
    return out


def fetch_listings(target_counties=config.TARGET_COUNTIES):
    rows = fetch_raw_rows()
    return to_raw_listings(rows, target_counties=target_counties)
