"""
Sailboat viability classifier: combines oceanfront determination, NOAA
depth sampling, OSM mooring/marina proximity, and a bridge/overhead-cable
flag into a PASS/FAIL/UNKNOWN result per listing.

This is a heuristic screen, not a substitute for local knowledge, a paper
chart, or a depth sounder. See README "Known limitations".
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from . import config, geo_context, bathymetry
from .geomath import bearing_deg

log = logging.getLogger(__name__)

OCEANFRONT_MAX_DISTANCE_M = 300
BRIDGE_BEARING_TOLERANCE_DEG = 60
# The direct property->nearest-coastline-point bearing can, near coves and
# headlands, run along or back onto land instead of out to open water.
# Sampling a small fan around it and keeping the best result guards against
# that specific false-negative failure mode without a full routing engine.
DEPTH_FAN_OFFSETS_DEG = [0, -20, 20, -40, 40]


@dataclass
class ViabilityAssessment:
    is_oceanfront: Optional[bool]  # None = undetermined (data source failure)
    oceanfront_note: str
    overall: str  # "PASS" | "FAIL" | "UNKNOWN" | "N/A" (not oceanfront)
    measured_depth_ft: Optional[float] = None
    distance_to_9ft_m: Optional[float] = None
    distance_to_shore_m: Optional[float] = None
    mooring_nearby: bool = False
    mooring_distance_m: Optional[float] = None
    bridge_flag: bool = False
    bridge_note: Optional[str] = None
    datum_source: Optional[str] = None
    notes: list = field(default_factory=list)


def _bearing_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _check_bridge_flag(lat, lon, ctx: geo_context.GeoContext, bearing_to_water):
    feat = ctx.nearest_bridge_or_powerline
    if feat is None or feat.distance_m > config.BRIDGE_SEARCH_RADIUS_M:
        return False, None
    b = bearing_deg(lat, lon, feat.lat, feat.lon)
    if _bearing_diff(b, bearing_to_water) > BRIDGE_BEARING_TOLERANCE_DEG:
        return False, None
    tags = feat.tags
    clearance = tags.get("maxheight") or tags.get("seamark:bridge:clearance_height")
    kind = "power line" if tags.get("power") == "line" else "bridge"
    note = f"upriver — verify overhead clearances ({kind} ~{feat.distance_m:.0f}m away, roughly between property and open water"
    if clearance:
        note += f", charted/tagged clearance: {clearance}"
    else:
        note += "; no charted clearance found in OSM data"
    note += f"; mast height assumption {config.MAST_HEIGHT_FT}ft)"
    return True, note


def _best_depth_over_fan(shore_lat, shore_lon, base_bearing):
    """Try a small fan of bearings around the direct property->shoreline
    bearing and keep the most favorable result (shortest distance to 9ft;
    if none reach 9ft, the one with the greatest max depth found)."""
    results = [
        bathymetry.sample_depth_along_bearing(shore_lat, shore_lon, (base_bearing + off) % 360)
        for off in DEPTH_FAN_OFFSETS_DEG
    ]
    passing = [r for r in results if r.distance_to_9ft_m is not None]
    if passing:
        return min(passing, key=lambda r: r.distance_to_9ft_m)
    with_depth = [r for r in results if r.max_depth_ft_mllw is not None]
    if with_depth:
        return max(with_depth, key=lambda r: r.max_depth_ft_mllw)
    return results[0]


def assess_listing(lat, lon) -> ViabilityAssessment:
    ctx = geo_context.fetch_geo_context(lat, lon)

    if not ctx.query_ok:
        return ViabilityAssessment(
            is_oceanfront=None,
            oceanfront_note=f"Could not determine oceanfront status: {ctx.error}",
            overall="UNKNOWN",
            notes=["Geo data source (OpenStreetMap Overpass) was unreachable for this listing."],
        )

    if ctx.nearest_saltwater is None or ctx.nearest_saltwater.distance_m > OCEANFRONT_MAX_DISTANCE_M:
        if ctx.nearest_freshwater is not None:
            note = (
                f"Nearest water is freshwater ({ctx.nearest_freshwater.tags.get('water', 'pond/lake')}) "
                f"{ctx.nearest_freshwater.distance_m:.0f}m away; no saltwater within {OCEANFRONT_MAX_DISTANCE_M}m."
            )
        else:
            note = f"No saltwater coastline or tidal water found within {OCEANFRONT_MAX_DISTANCE_M}m."
        return ViabilityAssessment(is_oceanfront=False, oceanfront_note=note, overall="N/A")

    shore = ctx.nearest_saltwater
    bearing_to_water = bearing_deg(lat, lon, shore.lat, shore.lon)
    oceanfront_note = f"Saltwater ({shore.tags.get('natural') or shore.tags.get('waterway') or 'water'}) {shore.distance_m:.0f}m from geocoded point."

    depth = _best_depth_over_fan(shore.lat, shore.lon, bearing_to_water)
    depth_pass = depth.distance_to_9ft_m is not None
    any_real_samples = any(s.depth_ft_mllw is not None for s in depth.samples)

    mooring = ctx.nearest_mooring_or_marina
    mooring_nearby = mooring is not None and mooring.distance_m <= config.MOORING_SEARCH_RADIUS_M

    bridge_flag, bridge_note = _check_bridge_flag(lat, lon, ctx, bearing_to_water)

    notes = []
    if depth_pass and mooring_nearby:
        overall = "PASS"
        notes.append("Depth reaches 9ft MLLW within 150m of shore and a mooring field/marina is charted nearby.")
    elif depth_pass and not mooring_nearby:
        overall = "PASS"
        notes.append(
            "Deep but possibly exposed — depth reaches 9ft MLLW within 150m of shore, but no charted "
            "mooring field or marina within 1km. Wave/wind exposure cannot be verified programmatically; use judgment."
        )
    elif not depth_pass and mooring_nearby:
        overall = "PASS"
        notes.append(
            "Bathymetry sampling along the heuristic bearing did not confirm 9ft MLLW within 150m of shore, "
            "but a charted mooring field/marina nearby is a strong positive signal that the area is used by boats. "
            "Bathymetry sampling can miss the actual channel; verify locally."
        )
    elif any_real_samples:
        overall = "FAIL"
        notes.append(
            f"Sampled depth did not reach {config.MIN_DEPTH_FT_MLLW:.0f}ft MLLW within "
            f"{config.SHORELINE_SEARCH_RADIUS_M}m of shore (max found: "
            f"{depth.max_depth_ft_mllw:.1f}ft) and no mooring field/marina charted within 1km."
        )
    else:
        overall = "UNKNOWN"
        notes.append(
            "No usable bathymetry data along the sampled bearing (NOAA DEM coverage gap) and no mooring "
            "field/marina charted within 1km. Cannot confirm or rule out viability."
        )

    if bridge_flag:
        notes.append(bridge_note)

    return ViabilityAssessment(
        is_oceanfront=True,
        oceanfront_note=oceanfront_note,
        overall=overall,
        measured_depth_ft=depth.max_depth_ft_mllw,
        distance_to_9ft_m=depth.distance_to_9ft_m,
        distance_to_shore_m=shore.distance_m,
        mooring_nearby=mooring_nearby,
        mooring_distance_m=mooring.distance_m if mooring else None,
        bridge_flag=bridge_flag,
        bridge_note=bridge_note,
        datum_source=depth.datum_source,
        notes=notes,
    )
