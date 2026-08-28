"""
Combined OpenStreetMap Overpass query for a listing's shoreline/water
context: nearest saltwater coastline/tidal water, nearby moorings/marinas,
and nearby bridges/power lines that could gate access to open water.

One Overpass call per listing, radius 1200m (covers the 1km mooring-
proximity radius plus margin), used for oceanfront classification, the
mooring-field proxy signal, and the upriver/bridge flag.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from .geomath import haversine_m, point_to_segment_distance_m, bearing_deg
from .httpclient import post_form_json

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
QUERY_RADIUS_M = 1200

_QUERY_TMPL = """
[out:json][timeout:25];
(
  way["natural"="coastline"](around:{r},{lat},{lon});
  way["natural"="water"](around:{r},{lat},{lon});
  relation["natural"="water"](around:{r},{lat},{lon});
  way["waterway"~"river|stream|tidal_channel"](around:{r},{lat},{lon});
  node["leisure"="marina"](around:{r},{lat},{lon});
  way["leisure"="marina"](around:{r},{lat},{lon});
  node["seamark:type"="mooring"](around:{r},{lat},{lon});
  node["mooring"](around:{r},{lat},{lon});
  way["man_made"="bridge"](around:{r},{lat},{lon});
  way["bridge"="yes"](around:{r},{lat},{lon});
  way["power"="line"](around:{r},{lat},{lon});
);
out geom;
"""


@dataclass
class NearestFeature:
    distance_m: float
    lat: float
    lon: float
    tags: dict = field(default_factory=dict)


@dataclass
class GeoContext:
    lat: float
    lon: float
    nearest_saltwater: Optional[NearestFeature]  # coastline or tidal waterway/bay
    nearest_freshwater: Optional[NearestFeature]  # inland water body, no tidal/coastline tag
    nearest_mooring_or_marina: Optional[NearestFeature]
    nearest_bridge_or_powerline: Optional[NearestFeature]
    query_ok: bool
    error: Optional[str] = None

    @property
    def is_saltwater_nearby(self) -> bool:
        return self.nearest_saltwater is not None

    @property
    def saltwater_only_freshwater_nearby(self) -> bool:
        return self.nearest_saltwater is None and self.nearest_freshwater is not None


def _way_nearest_point(lat, lon, geometry):
    """Nearest distance (m) and point from (lat, lon) to a way's polyline,
    given Overpass 'out geom' geometry (list of {lat, lon})."""
    if not geometry:
        return None
    if len(geometry) == 1:
        d = haversine_m(lat, lon, geometry[0]["lat"], geometry[0]["lon"])
        return d, geometry[0]["lat"], geometry[0]["lon"]
    best = None
    for a, b in zip(geometry, geometry[1:]):
        d = point_to_segment_distance_m(lat, lon, a["lat"], a["lon"], b["lat"], b["lon"])
        if best is None or d < best[0]:
            # Use the closer endpoint for the representative lat/lon.
            da = haversine_m(lat, lon, a["lat"], a["lon"])
            db = haversine_m(lat, lon, b["lat"], b["lon"])
            pt = (a["lat"], a["lon"]) if da <= db else (b["lat"], b["lon"])
            best = (d, pt[0], pt[1])
    return best


_TIDAL_WATERWAY_TAGS = {"tidal_channel"}


def _is_tidal_waterway(tags):
    if tags.get("waterway") in _TIDAL_WATERWAY_TAGS:
        return True
    if tags.get("tidal") == "yes":
        return True
    return False


def _is_saltwater_water_polygon(tags):
    # OSM tags bays/straits/harbors distinctly from inland lakes/ponds.
    water = tags.get("water", "")
    natural = tags.get("natural", "")
    if natural == "bay":
        return True
    if water in {"bay", "cove", "harbour", "harbor", "strait"}:
        return True
    if tags.get("tidal") == "yes":
        return True
    return False


def _is_freshwater_water_polygon(tags):
    water = tags.get("water", "")
    return water in {"lake", "pond", "reservoir"} or tags.get("natural") == "water" and water == ""


def fetch_geo_context(lat, lon, radius_m=QUERY_RADIUS_M) -> GeoContext:
    query = _QUERY_TMPL.format(r=radius_m, lat=lat, lon=lon)
    try:
        data = post_form_json(OVERPASS_URL, data={"data": query})
    except Exception as e:  # noqa: BLE001
        log.warning("Overpass query failed for (%s, %s): %s", lat, lon, e)
        return GeoContext(lat, lon, None, None, None, None, query_ok=False, error=str(e))

    nearest_salt = None
    nearest_fresh = None
    nearest_mooring = None
    nearest_bridge = None

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        etype = el.get("type")

        if etype == "node":
            d = haversine_m(lat, lon, el["lat"], el["lon"])
            feat = NearestFeature(d, el["lat"], el["lon"], tags)
            if tags.get("leisure") == "marina" or tags.get("seamark:type") == "mooring" or "mooring" in tags:
                if nearest_mooring is None or d < nearest_mooring.distance_m:
                    nearest_mooring = feat
            continue

        geometry = el.get("geometry")
        if not geometry:
            continue
        result = _way_nearest_point(lat, lon, geometry)
        if result is None:
            continue
        d, flat, flon = result
        feat = NearestFeature(d, flat, flon, tags)

        if tags.get("natural") == "coastline":
            if nearest_salt is None or d < nearest_salt.distance_m:
                nearest_salt = feat
        elif tags.get("waterway") and (tags.get("waterway") in {"river", "stream", "tidal_channel"}):
            if _is_tidal_waterway(tags):
                if nearest_salt is None or d < nearest_salt.distance_m:
                    nearest_salt = feat
            # Non-tidal-tagged rivers are ambiguous (most rivers aren't
            # tagged tidal=yes in OSM even when they are); we don't classify
            # them as fresh or salt on their own, only via the coastline/bay
            # signal, to avoid falsely excluding tidal-river properties.
        elif tags.get("leisure") == "marina":
            if nearest_mooring is None or d < nearest_mooring.distance_m:
                nearest_mooring = feat
        elif tags.get("natural") == "water" or tags.get("water"):
            if _is_saltwater_water_polygon(tags):
                if nearest_salt is None or d < nearest_salt.distance_m:
                    nearest_salt = feat
            elif _is_freshwater_water_polygon(tags):
                if nearest_fresh is None or d < nearest_fresh.distance_m:
                    nearest_fresh = feat
        elif tags.get("man_made") == "bridge" or tags.get("bridge") == "yes" or tags.get("power") == "line":
            if nearest_bridge is None or d < nearest_bridge.distance_m:
                nearest_bridge = feat

    return GeoContext(
        lat=lat, lon=lon,
        nearest_saltwater=nearest_salt,
        nearest_freshwater=nearest_fresh,
        nearest_mooring_or_marina=nearest_mooring,
        nearest_bridge_or_powerline=nearest_bridge,
        query_ok=True,
    )


def bearing_to_saltwater(ctx: GeoContext):
    if ctx.nearest_saltwater is None:
        return None
    return bearing_deg(ctx.lat, ctx.lon, ctx.nearest_saltwater.lat, ctx.nearest_saltwater.lon)
