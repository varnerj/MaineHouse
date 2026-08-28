"""SQLite-backed state store: every listing ever seen, with price history
and cached viability results (so geo/bathymetry lookups only ever run once
per listing, not on every daily run)."""
import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id TEXT PRIMARY KEY,
    address TEXT, city TEXT, county TEXT, state TEXT, zip TEXT,
    price INTEGER, beds REAL, baths REAL, sqft INTEGER, lot_size_sqft INTEGER,
    year_built INTEGER, days_on_market INTEGER, status TEXT, url TEXT,
    mls_number TEXT, source TEXT, lat REAL, lon REAL, property_type TEXT,

    is_oceanfront INTEGER,
    oceanfront_note TEXT,
    viability_overall TEXT,
    measured_depth_ft REAL, distance_to_9ft_m REAL, distance_to_shore_m REAL,
    mooring_nearby INTEGER, mooring_distance_m REAL,
    bridge_flag INTEGER, bridge_note TEXT, datum_source TEXT,
    viability_notes TEXT,

    price_history TEXT,
    first_seen_date TEXT, last_seen_date TEXT, removed_date TEXT
);
"""


@contextmanager
def connect(db_path=config.STATE_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_row(conn, listing_id):
    cur = conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,))
    return cur.fetchone()


def get_all_active_reportable(conn):
    """Rows currently eligible for reporting: PASS or UNKNOWN viability
    (confidently-not-oceanfront "N/A" and confidently-FAIL rows are
    excluded; UNKNOWN covers both "can't confirm viability" and "can't even
    confirm oceanfront status" -- neither is silently dropped), priced at or
    below the upper band, not marked removed."""
    cur = conn.execute(
        """
        SELECT * FROM listings
        WHERE viability_overall IN ('PASS', 'UNKNOWN')
          AND price <= ?
          AND removed_date IS NULL
        """,
        (config.PRICE_UPPER_MAX,),
    )
    return cur.fetchall()


def upsert_listing(conn, raw_listing, assessment, today_str):
    """Insert or update a listing row from this run's fetch + freshly (or
    previously) computed viability assessment. Returns (is_new, old_price)."""
    listing_id = raw_listing.listing_id
    existing = get_row(conn, listing_id)

    price_history = json.loads(existing["price_history"]) if existing else []
    old_price = existing["price"] if existing else None
    if not price_history or price_history[-1][1] != raw_listing.price:
        price_history.append([today_str, raw_listing.price])

    fields = dict(
        listing_id=listing_id,
        address=raw_listing.address, city=raw_listing.city, county=raw_listing.county,
        state=raw_listing.state, zip=raw_listing.zip,
        price=raw_listing.price, beds=raw_listing.beds, baths=raw_listing.baths,
        sqft=raw_listing.sqft, lot_size_sqft=raw_listing.lot_size_sqft,
        year_built=raw_listing.year_built, days_on_market=raw_listing.days_on_market,
        status=raw_listing.status, url=raw_listing.url, mls_number=raw_listing.mls_number,
        source=raw_listing.source, lat=raw_listing.lat, lon=raw_listing.lon,
        property_type=raw_listing.property_type,

        is_oceanfront=int(bool(assessment.is_oceanfront)) if assessment.is_oceanfront is not None else None,
        oceanfront_note=assessment.oceanfront_note,
        viability_overall=assessment.overall,
        measured_depth_ft=assessment.measured_depth_ft,
        distance_to_9ft_m=assessment.distance_to_9ft_m,
        distance_to_shore_m=assessment.distance_to_shore_m,
        mooring_nearby=int(assessment.mooring_nearby),
        mooring_distance_m=assessment.mooring_distance_m,
        bridge_flag=int(assessment.bridge_flag),
        bridge_note=assessment.bridge_note,
        datum_source=assessment.datum_source,
        viability_notes=json.dumps(assessment.notes),

        price_history=json.dumps(price_history),
        first_seen_date=existing["first_seen_date"] if existing else today_str,
        last_seen_date=today_str,
        removed_date=None,  # reappeared or still active; clear any prior removal
    )

    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields if k != "listing_id")
    conn.execute(
        f"INSERT INTO listings ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(listing_id) DO UPDATE SET {updates}",
        list(fields.values()),
    )
    return existing is None, old_price


def mark_removed(conn, listing_id, today_str):
    conn.execute(
        "UPDATE listings SET removed_date = ? WHERE listing_id = ? AND removed_date IS NULL",
        (today_str, listing_id),
    )
