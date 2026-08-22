"""Geographic maths: polylines, route matching, map viewports.

Coordinates are ``(lat, lon)`` degree pairs throughout — the order Plotly's
map traces and every mapping library expect. FIT stores semicircles; the
conversion happens once, in ``ingest/fit.py``.
"""

import math

import numpy as np

from .arrays import FloatArray

EARTH_RADIUS_M = 6_371_008.8

# Web Mercator tile size, used by MapLibre and every XYZ tile scheme.
TILE_SIZE_PX = 256

Point = tuple[float, float]


def haversine_m(a: Point, b: Point) -> float:
    """Great-circle distance between two ``(lat, lon)`` points, in metres."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def _perpendicular_distance_m(point: Point, start: Point, end: Point) -> float:
    """Distance from ``point`` to the segment ``start``-``end``, in metres.

    Projects onto a local equirectangular plane centred on ``start``. Over the
    few hundred metres a polyline segment spans, that is accurate to well
    under the simplification tolerance and avoids a great-circle solve per
    point per recursion level.
    """
    lat_scale = math.cos(math.radians(start[0]))
    px = (point[1] - start[1]) * lat_scale
    py = point[0] - start[0]
    ex = (end[1] - start[1]) * lat_scale
    ey = end[0] - start[0]

    segment_sq = ex * ex + ey * ey
    if segment_sq == 0.0:
        return haversine_m(point, start)

    t = max(0.0, min(1.0, (px * ex + py * ey) / segment_sq))
    dx, dy = px - t * ex, py - t * ey
    return math.hypot(dx, dy) * math.radians(1.0) * EARTH_RADIUS_M


def simplify_polyline(points: list[Point], epsilon_m: float = 10.0) -> list[Point]:
    """Douglas-Peucker simplification, keeping the shape within ``epsilon_m``.

    Simplification is mandatory rather than an optimisation. A ride is ~10,000
    raw points; the dashboard heat map draws every route at once, and a Rides
    list draws a preview per card. Feeding raw tracks to either stalls the
    renderer.

    Iterative rather than recursive: a 10,000-point track can nest deeply
    enough to exhaust the interpreter's stack.
    """
    if len(points) < 3 or epsilon_m <= 0:
        return list(points)

    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        furthest_index, furthest_distance = first, -1.0
        for i in range(first + 1, last):
            distance = _perpendicular_distance_m(points[i], points[first], points[last])
            if distance > furthest_distance:
                furthest_index, furthest_distance = i, distance
        if furthest_distance > epsilon_m:
            keep[furthest_index] = True
            stack.append((first, furthest_index))
            stack.append((furthest_index, last))

    return [p for p, keep_it in zip(points, keep, strict=True) if keep_it]


def resample_polyline(points: list[Point], count: int) -> list[Point]:
    """Resample to exactly ``count`` points, evenly spaced along the track.

    Even spacing by arc length rather than by index is what makes
    :func:`route_match_score` insensitive to speed: two rides along the same
    road match even when one of them stopped at every light.
    """
    if count <= 0 or not points:
        return []
    if len(points) == 1:
        return [points[0]] * count

    segment_lengths = [haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1)]
    cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
    total = float(cumulative[-1])
    if total <= 0:
        return [points[0]] * count

    targets = np.linspace(0.0, total, count)
    lats = np.interp(targets, cumulative, [p[0] for p in points])
    lons = np.interp(targets, cumulative, [p[1] for p in points])
    return [(float(a), float(o)) for a, o in zip(lats, lons, strict=True)]


def _coverage(a: list[Point], b: list[Point], tolerance_m: float) -> float:
    """Fraction of ``a``'s points with some point of ``b`` within tolerance."""
    if not a or not b:
        return 0.0
    b_lat = np.array([p[0] for p in b])
    b_lon = np.array([p[1] for p in b])
    mean_lat = math.radians(float(np.mean(b_lat)))
    lat_scale = math.cos(mean_lat)
    metres_per_degree = math.radians(1.0) * EARTH_RADIUS_M

    hits = 0
    for lat, lon in a:
        dy = (b_lat - lat) * metres_per_degree
        dx = (b_lon - lon) * lat_scale * metres_per_degree
        if float(np.min(dx * dx + dy * dy)) <= tolerance_m * tolerance_m:
            hits += 1
    return hits / len(a)


def route_match_score(
    a: list[Point],
    b: list[Point],
    tolerance_m: float = 60.0,
    samples: int = 200,
) -> float:
    """How well two tracks describe the same route, from 0 to 1.

    Both tracks are resampled to the same number of evenly spaced points, then
    each is asked what fraction of its points lie within ``tolerance_m`` of the
    other. The score is the **smaller** of the two fractions, which is what
    makes it symmetric and what stops a short ride scoring 1.0 merely because
    it is entirely contained in a long one.

    ``tolerance_m`` is deliberately loose. GPS wanders by tens of metres under
    tree cover, and the two sides of a dual carriageway are the same route.
    """
    if not a or not b:
        return 0.0
    resampled_a = resample_polyline(a, samples)
    resampled_b = resample_polyline(b, samples)
    forward = _coverage(resampled_a, resampled_b, tolerance_m)
    backward = _coverage(resampled_b, resampled_a, tolerance_m)
    return min(forward, backward)


def polyline_length_m(points: list[Point]) -> float:
    """Total length of a track in metres."""
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def bounds_to_center_zoom(
    points: list[Point],
    width_px: int = 900,
    height_px: int = 500,
    padding: float = 1.15,
    max_zoom: float = 16.0,
) -> tuple[float, float, float]:
    """Viewport ``(lat, lon, zoom)`` that frames ``points``.

    Plotly's map has no ``fit_bounds()``, so the viewport has to be computed.
    Doing it as maths rather than by eye is the point: it is pure, so it is
    unit-testable, and the same numbers work for the ride map, the route map
    and the dashboard heat map.

    ``padding`` > 1 leaves a margin so the track does not touch the edges.
    """
    if not points:
        return 0.0, 0.0, 1.0

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    center_lat = (min_lat + max_lat) / 2.0
    center_lon = (min_lon + max_lon) / 2.0

    lon_span = (max_lon - min_lon) * padding
    # Compare latitudes in Mercator-projected space, where degrees of latitude
    # are not of constant height — otherwise tracks far from the equator get
    # framed too tightly.
    lat_span = (_mercator_y(max_lat) - _mercator_y(min_lat)) * padding

    zooms: list[float] = []
    if lon_span > 0:
        zooms.append(math.log2(360.0 / lon_span * width_px / TILE_SIZE_PX))
    if lat_span > 0:
        zooms.append(math.log2(360.0 / lat_span * height_px / TILE_SIZE_PX))

    # A single point (or a perfectly straight north-south line) has no span in
    # one axis; fall back to the closest allowed zoom rather than dividing by
    # zero or handing Plotly an infinity.
    zoom = min(zooms) if zooms else max_zoom
    return center_lat, center_lon, float(min(max(zoom, 0.0), max_zoom))


def _mercator_y(lat: float) -> float:
    """Web Mercator northing for a latitude, in degree-equivalent units."""
    clamped = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(clamped)
    return math.degrees(math.log(math.tan(math.pi / 4 + radians / 2)))


def bounding_box(points: list[Point]) -> tuple[float, float, float, float]:
    """``(min_lat, min_lon, max_lat, max_lon)`` for a track."""
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return min(lats), min(lons), max(lats), max(lons)


def to_points(lat: FloatArray, lon: FloatArray) -> list[Point]:
    """Zip two coordinate channels into points, dropping samples with no fix."""
    count = min(len(lat), len(lon))
    return [
        (float(lat[i]), float(lon[i]))
        for i in range(count)
        if np.isfinite(lat[i]) and np.isfinite(lon[i])
    ]


# ---------------------------------------------------------------------------
# SVG thumbnails
# ---------------------------------------------------------------------------

# Coordinate space of a list-card preview. Tracks are projected into this box
# once, at import, and stored as a string — see ``Ride.preview_points``.
PREVIEW_VIEWBOX = 100.0
PREVIEW_PADDING = 6.0


def project_svg_points(
    points: list[Point],
    max_points: int = 120,
    box: float = PREVIEW_VIEWBOX,
    padding: float = PREVIEW_PADDING,
) -> str:
    """Project a track into an SVG ``points`` attribute string.

    Longitude is scaled by ``cos(latitude)`` and the aspect ratio is
    preserved, so the thumbnail keeps the shape a rider would recognise —
    without the cosine every track is stretched east-west by about a third at
    these latitudes, and without the aspect lock an out-and-back becomes a
    blob.

    Pure maths, and therefore here rather than in ``components/``: import
    precomputes this string per ride, and list cards render inside
    ``rx.foreach`` where no per-item Python runs.

    Returns an empty string when there is nothing to draw, which is the card's
    cue to show a placeholder.
    """
    if len(points) < 2:
        return ""

    thinned = _thin(points, max_points)
    mean_lat_rad = math.radians(sum(p[0] for p in thinned) / len(thinned))
    xs = [p[1] * math.cos(mean_lat_rad) for p in thinned]
    ys = [-p[0] for p in thinned]  # SVG y grows downward; north must be up.

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y)
    if span <= 0:
        return ""

    usable = box - 2 * padding
    scale = usable / span
    offset_x = padding + (usable - (max_x - min_x) * scale) / 2
    offset_y = padding + (usable - (max_y - min_y) * scale) / 2

    return " ".join(
        f"{offset_x + (x - min_x) * scale:.1f},{offset_y + (y - min_y) * scale:.1f}"
        for x, y in zip(xs, ys, strict=True)
    )


def _thin(points: list[Point], limit: int) -> list[Point]:
    """Keep at most ``limit`` evenly spaced points, endpoints included."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    thinned = [points[int(i * step)] for i in range(limit)]
    thinned[-1] = points[-1]
    return thinned
