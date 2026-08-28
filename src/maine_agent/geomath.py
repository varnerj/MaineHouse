"""Small geodesy helpers (no external dependencies)."""
import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def destination_point(lat, lon, bearing_deg_, distance_m):
    """Point at `distance_m` meters from (lat, lon) along `bearing_deg_`."""
    delta = distance_m / EARTH_RADIUS_M
    theta = math.radians(bearing_deg_)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)

    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lambda2) + 540) % 360 - 180


def point_to_segment_distance_m(p_lat, p_lon, a_lat, a_lon, b_lat, b_lon):
    """Approximate distance from point P to segment AB, projecting to a
    local equirectangular plane (fine at this scale, sub-km segments)."""
    lat0 = math.radians((a_lat + b_lat) / 2)
    mx = math.cos(lat0) * EARTH_RADIUS_M

    def to_xy(lat, lon):
        return (math.radians(lon) * mx, math.radians(lat) * EARTH_RADIUS_M)

    px, py = to_xy(p_lat, p_lon)
    ax, ay = to_xy(a_lat, a_lon)
    bx, by = to_xy(b_lat, b_lon)

    abx, aby = bx - ax, by - ay
    ab_len2 = abx * abx + aby * aby
    if ab_len2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / ab_len2))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)


def feet_to_meters(ft):
    return ft * 0.3048


def meters_to_feet(m):
    return m / 0.3048
