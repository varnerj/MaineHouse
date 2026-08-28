"""One end-to-end run: fetch Redfin listings, compute (or reuse cached)
viability, persist to state, and classify what changed since the last run."""
import json
import logging
import time
import types
from dataclasses import dataclass, field
from datetime import date

from . import config, redfin, state, viability

log = logging.getLogger(__name__)


@dataclass
class DiffResult:
    new_under_main: list = field(default_factory=list)       # (row,)
    price_cuts_under_main: list = field(default_factory=list)  # (row, old_price)
    above_budget_passes: list = field(default_factory=list)  # (row, old_price_or_None, is_new)
    removed: list = field(default_factory=list)               # (row,)
    unverified: list = field(default_factory=list)            # (row, old_price_or_None, is_new)
    run_warnings: list = field(default_factory=list)
    total_candidates_considered: int = 0
    total_reportable_tracked: int = 0


def _cached_assessment_from_row(row):
    return types.SimpleNamespace(
        is_oceanfront=bool(row["is_oceanfront"]) if row["is_oceanfront"] is not None else None,
        oceanfront_note=row["oceanfront_note"],
        overall=row["viability_overall"],
        measured_depth_ft=row["measured_depth_ft"],
        distance_to_9ft_m=row["distance_to_9ft_m"],
        distance_to_shore_m=row["distance_to_shore_m"],
        mooring_nearby=bool(row["mooring_nearby"]),
        mooring_distance_m=row["mooring_distance_m"],
        bridge_flag=bool(row["bridge_flag"]),
        bridge_note=row["bridge_note"],
        datum_source=row["datum_source"],
        notes=json.loads(row["viability_notes"]) if row["viability_notes"] else [],
    )


def run(max_new_assessments=config.MAX_NEW_ASSESSMENTS_PER_RUN, sleep_between_assessments_s=0.2, commit_every=20):
    """Execute one full pipeline run. Returns a DiffResult.

    max_new_assessments caps the number of *newly-seen* listings assessed
    this run (real NOAA/Overpass work); price/status refresh of already-
    cached listings is unaffected and always runs on the full candidate
    set. This bounds run duration -- see MAX_NEW_ASSESSMENTS_PER_RUN in
    config.py for why. Pass a small value (e.g. 5-20) for fast local
    testing, or None for no cap.

    Commits periodically (every `commit_every` processed listings) so a
    run that dies partway through (network hiccup, timeout) doesn't lose
    the already-computed viability assessments -- those are the expensive
    part (NOAA/Overpass calls), and re-running from scratch would both
    waste that work and hammer the free upstream services again.
    """
    warnings = []
    today_str = date.today().isoformat()

    try:
        raw_listings = redfin.fetch_listings()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Redfin fetch failed: {e}") from e

    candidates = [l for l in raw_listings if l.price is not None and l.price <= config.PRICE_UPPER_MAX]
    skipped_no_price = [l for l in raw_listings if l.price is None]
    if skipped_no_price:
        log.warning("Skipping %d listings with no parseable price", len(skipped_no_price))

    diff = DiffResult(total_candidates_considered=len(candidates))
    log.info("%d candidates to process (price <= $%d)", len(candidates), config.PRICE_UPPER_MAX)
    run_start = time.monotonic()

    with state.connect() as conn:
        previously_reportable = {r["listing_id"]: r for r in state.get_all_active_reportable(conn)}
        seen_ids = set()
        assessed_new_count = 0

        for i, listing in enumerate(candidates):
            if i and i % 50 == 0:
                elapsed = time.monotonic() - run_start
                rate = i / elapsed
                remaining_s = (len(candidates) - i) / rate if rate > 0 else float("inf")
                log.info(
                    "progress: %d/%d processed (%d newly assessed), %.1fs elapsed, "
                    "~%.0fs remaining at current rate",
                    i, len(candidates), assessed_new_count, elapsed, remaining_s,
                )
            seen_ids.add(listing.listing_id)
            existing_row = state.get_row(conn, listing.listing_id)
            has_cache = existing_row is not None and existing_row["viability_overall"] is not None

            if has_cache:
                assessment = _cached_assessment_from_row(existing_row)
            else:
                if max_new_assessments is not None and assessed_new_count >= max_new_assessments:
                    continue  # leave unassessed for now; will be picked up next run
                if listing.lat is None or listing.lon is None:
                    assessment = types.SimpleNamespace(
                        is_oceanfront=None,
                        oceanfront_note="No coordinates available from source data.",
                        overall="UNKNOWN",
                        measured_depth_ft=None, distance_to_9ft_m=None, distance_to_shore_m=None,
                        mooring_nearby=False, mooring_distance_m=None,
                        bridge_flag=False, bridge_note=None, datum_source=None,
                        notes=["Listing had no usable latitude/longitude; oceanfront and depth checks could not run."],
                    )
                else:
                    try:
                        assessment = viability.assess_listing(listing.lat, listing.lon)
                    except Exception as e:  # noqa: BLE001
                        log.warning("Viability assessment failed for %s: %s", listing.listing_id, e)
                        assessment = types.SimpleNamespace(
                            is_oceanfront=None,
                            oceanfront_note=f"Assessment error: {e}",
                            overall="UNKNOWN",
                            measured_depth_ft=None, distance_to_9ft_m=None, distance_to_shore_m=None,
                            mooring_nearby=False, mooring_distance_m=None,
                            bridge_flag=False, bridge_note=None, datum_source=None,
                            notes=[f"Viability assessment raised an error: {e}"],
                        )
                assessed_new_count += 1
                if sleep_between_assessments_s:
                    time.sleep(sleep_between_assessments_s)

            is_new, old_price = state.upsert_listing(conn, listing, assessment, today_str)

            reportable = assessment.overall in ("PASS", "UNKNOWN")
            if not reportable:
                continue

            row = state.get_row(conn, listing.listing_id)
            price = listing.price
            band_main = price <= config.PRICE_MAIN_MAX
            price_changed = (not is_new) and old_price is not None and old_price != price

            if assessment.overall == "UNKNOWN":
                if is_new or price_changed:
                    diff.unverified.append((row, old_price, is_new))
                continue

            # PASS
            if band_main:
                if is_new:
                    diff.new_under_main.append((row,))
                elif price_changed and price < old_price:
                    diff.price_cuts_under_main.append((row, old_price))
            else:
                if is_new or price_changed:
                    diff.above_budget_passes.append((row, old_price, is_new))

            if commit_every and (i + 1) % commit_every == 0:
                conn.commit()

        # Anything previously reportable+active that we did not see at all
        # this run has left Redfin's active result set (sold/pending/
        # withdrawn -- the CSV export doesn't distinguish which).
        for listing_id, row in previously_reportable.items():
            if listing_id not in seen_ids:
                state.mark_removed(conn, listing_id, today_str)
                diff.removed.append((row,))

        diff.total_reportable_tracked = len(state.get_all_active_reportable(conn))

    diff.run_warnings = warnings
    return diff
