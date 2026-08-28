"""
Depth-at-shoreline check using free NOAA data:

  - NOAA NCEI "DEM_all" ImageServer (CUDEM topobathy mosaic among other
    sources), queried per-point via its `identify` REST operation. No bulk
    tile download needed. Vertical datum is NAVD88.
  - NOAA VDatum API, queried per-listing to get the local NAVD88->MLLW
    offset (this varies meaningfully across the ~150km of coastline in
    scope, so a single blanket constant would be materially wrong in
    places; VDatum gives the real per-point value for free).

KNOWN LIMITATIONS (see README): CUDEM mosaic tiles vary in source/resolution
and are not guaranteed current; a DEM pixel can be NoData directly at the
shoreline exactly where MLLW conversion is needed; the sampled point is a
grid cell average, not a survey sounding. This is a heuristic screen, not a
substitute for a chart or a depth sounder.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from . import config
from .geomath import destination_point, meters_to_feet
from .httpclient import get_json

log = logging.getLogger(__name__)

DEM_IDENTIFY_URL = "https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_all/ImageServer/identify"
VDATUM_URL = "https://vdatum.noaa.gov/vdatumweb/api/convert"

_vdatum_cache = {}


def get_navd88_elevation_m(lat, lon) -> Optional[float]:
    """Elevation in meters relative to NAVD88 at (lat, lon). Negative values
    are below the datum (typically underwater bathymetry). Returns None if
    no dataset covers the point (NoData) or the request fails."""
    params = {
        "geometry": f'{{"x":{lon},"y":{lat},"spatialReference":{{"wkid":4326}}}}',
        "geometryType": "esriGeometryPoint",
        "sr": 4326,
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        data = get_json(DEM_IDENTIFY_URL, params=params)
    except Exception as e:  # noqa: BLE001
        log.warning("DEM identify failed for (%s, %s): %s", lat, lon, e)
        return None
    value = data.get("value")
    if value is None or value == "NoData":
        return None
    try:
        elev = float(value)
    except (TypeError, ValueError):
        return None
    # Guard against raw NoData sentinels some ImageServer layers return as
    # a numeric fill value (e.g. -3.4e38) instead of the string "NoData".
    # No real elevation near the Maine coast approaches this magnitude.
    if abs(elev) > 5000:
        return None
    return elev


def get_navd88_to_mllw_offset_m(lat, lon):
    """Meters that MLLW sits below NAVD88 at (lat, lon): subtract this from
    a NAVD88-referenced depth to get the MLLW-referenced (charted) depth.
    Returns (offset_m, source) where source is "vdatum" or
    "fallback_constant". Cached per ~1km grid cell (rounded to 2 decimal
    degrees) since it's smoothly varying tidal geometry, not point-specific.
    Falls back to a documented approximate constant if the VDatum API is
    unreachable."""
    key = (round(lat, 2), round(lon, 2))
    if key in _vdatum_cache:
        return _vdatum_cache[key]

    params = {
        "s_x": lon, "s_y": lat, "s_z": 0,
        "s_h_frame": "NAD83_2011", "s_v_frame": "NAVD88", "s_v_unit": "m",
        "s_v_geoid": "GEOID18",
        "t_v_frame": "MLLW", "t_v_unit": "m", "t_h_frame": "NAD83_2011",
        "region": "contiguous",
    }
    try:
        data = get_json(VDATUM_URL, params=params)
        offset = float(data["t_z"])
        # VDatum returns a -999999-style sentinel (documented NOAA fill
        # value) when the point has no valid tidal-datum grid coverage
        # (e.g. slightly inland/on land, or outside the transformation
        # region). A real NAVD88->MLLW offset on this coast is a few
        # meters; anything wildly outside that range is not real data.
        if not (-10.0 <= offset <= 10.0):
            raise ValueError(f"VDatum returned an out-of-range/sentinel offset: {offset}")
        result = (offset, "vdatum")
    except Exception as e:  # noqa: BLE001
        log.warning(
            "VDatum lookup failed for (%s, %s), using fallback offset %.2fm: %s",
            lat, lon, config.FALLBACK_NAVD88_TO_MLLW_OFFSET_M, e,
        )
        result = (config.FALLBACK_NAVD88_TO_MLLW_OFFSET_M, "fallback_constant")
    _vdatum_cache[key] = result
    return result


@dataclass
class DepthSample:
    distance_from_shore_m: float
    depth_ft_mllw: Optional[float]  # positive = underwater depth; None = NoData


@dataclass
class DepthCheckResult:
    samples: list
    max_depth_ft_mllw: Optional[float]
    distance_to_9ft_m: Optional[float]  # None if not reached within budget
    navd88_to_mllw_offset_m: float
    datum_source: str  # "vdatum" or "fallback_constant"


def sample_depth_along_bearing(shore_lat, shore_lon, bearing_deg, max_distance_m=config.SHORELINE_SEARCH_RADIUS_M, step_m=config.SAMPLE_STEP_M):
    offset_m, datum_source = get_navd88_to_mllw_offset_m(shore_lat, shore_lon)

    samples = []
    distance_to_9ft = None
    max_depth = None
    dist = 0.0
    while dist <= max_distance_m:
        plat, plon = destination_point(shore_lat, shore_lon, bearing_deg, dist)
        elev_navd88 = get_navd88_elevation_m(plat, plon)
        depth_ft = None
        if elev_navd88 is not None:
            depth_m_mllw = -elev_navd88 - offset_m
            depth_ft = meters_to_feet(depth_m_mllw)
            if max_depth is None or depth_ft > max_depth:
                max_depth = depth_ft
            if distance_to_9ft is None and depth_ft >= config.MIN_DEPTH_FT_MLLW:
                distance_to_9ft = dist
        samples.append(DepthSample(dist, depth_ft))
        if distance_to_9ft is not None:
            break
        dist += step_m

    return DepthCheckResult(
        samples=samples,
        max_depth_ft_mllw=max_depth,
        distance_to_9ft_m=distance_to_9ft,
        navd88_to_mllw_offset_m=offset_m,
        datum_source=datum_source,
    )
