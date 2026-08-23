"""Geographic maths: polylines, route matching, map viewports.

Coordinates are ``(lat, lon)`` degree pairs throughout — the order Plotly's
map traces and every mapping library expect. FIT stores semicircles; the
conversion happens once, in ``ingest/fit.py``.
"""

import math

import numpy as np

from .arrays import FloatArray

EARTH_RADIUS_M = 6_371_008.8

# Web Mercator tile size **as MapLibre defines zoom**, which is what Plotly's
# `map` subplot renders with. Do not "correct" this to 256: that is the
# Google/Leaflet convention, and using it makes every computed zoom exactly one
# level too high — half the intended span in each axis. Measured against the
# reference tracks, that put 174% of the track's height inside the viewport
# while the width still fitted, i.e. clipped top and bottom and nothing else.
TILE_SIZE_PX = 512

# Spacing between the probe points `route_match_score` tests a track at. A
# fixed *count* would make the spacing proportional to length, which is exactly
# the defect that let long routes never match; a fixed *spacing* keeps the
# check equally thorough at 6 km and at 200 km. The bounds cap the cost on a
# very long tour and keep a short track from being probed too coarsely.
MATCH_PROBE_SPACING_M = 25.0
MIN_MATCH_PROBES = 200
MAX_MATCH_PROBES = 4000

# How close a track's start and end must be for it to count as a loop.
LOOP_MARGIN_M = 500.0

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


def _to_local_metres(points: list[Point], lat_scale: float) -> np.ndarray:
    """Project ``(lat, lon)`` onto a local equirectangular plane, in metres.

    One shared ``lat_scale`` for both tracks, so the two are measured in the
    same plane — computing it per track would shear one relative to the other.
    """
    metres_per_degree = math.radians(1.0) * EARTH_RADIUS_M
    return np.array(
        [[p[0] * metres_per_degree, p[1] * lat_scale * metres_per_degree] for p in points],
        dtype=float,
    )


def _coverage(
    probes: list[Point], track: list[Point], tolerance_m: float, lat_scale: float
) -> float:
    """Fraction of ``probes`` lying within ``tolerance_m`` of ``track``'s **polyline**.

    Distance is measured to the track's *segments*, not to its vertices. That
    distinction is the whole correctness of :func:`route_match_score`: measured
    to vertices, the achievable distance is bounded below by half the vertex
    spacing, so the effective tolerance grows with track length and a long
    route can never match itself. See the note there.
    """
    if not probes or not track:
        return 0.0

    limit = tolerance_m * tolerance_m
    points = _to_local_metres(track, lat_scale)
    probe_points = _to_local_metres(probes, lat_scale)

    if len(points) == 1:
        # A degenerate one-point track has no segments; fall back to the point.
        distances = ((probe_points - points[0]) ** 2).sum(axis=1)
        return float(np.count_nonzero(distances <= limit)) / len(probes)

    starts = points[:-1]
    deltas = points[1:] - starts
    lengths_sq = (deltas * deltas).sum(axis=1)
    # A zero-length segment would divide by zero; the clamp below then pins
    # t = 0 and the distance is measured to the (coincident) endpoint anyway.
    lengths_sq[lengths_sq == 0.0] = 1e-12

    hits = 0
    for probe in probe_points:
        offsets = probe - starts
        t = np.clip((offsets * deltas).sum(axis=1) / lengths_sq, 0.0, 1.0)
        closest = starts + t[:, None] * deltas
        if float(((probe - closest) ** 2).sum(axis=1).min()) <= limit:
            hits += 1
    return hits / len(probes)


def _probe_count(points: list[Point]) -> int:
    """How many evenly spaced probes to test a track with.

    Derived from length at a fixed spatial spacing rather than being a
    constant, so the probe density — how thoroughly a track is checked — stays
    the same whether it is a 6 km commute or a 200 km tour. A constant count
    silently makes the spacing proportional to length instead.
    """
    length_m = polyline_length_m(points)
    wanted = int(length_m / MATCH_PROBE_SPACING_M)
    return max(MIN_MATCH_PROBES, min(wanted, MAX_MATCH_PROBES))


def route_match_score(
    a: list[Point],
    b: list[Point],
    tolerance_m: float = 60.0,
    samples: int | None = None,
) -> float:
    """How well two tracks describe the same route, from 0 to 1.

    Each track is probed at evenly spaced points and asked what fraction of
    those probes lie within ``tolerance_m`` of the **other track's polyline**.
    The score is the *smaller* of the two fractions, which is what makes it
    symmetric and what stops a short ride scoring 1.0 merely because it is
    entirely contained in a long one.

    ``tolerance_m`` is deliberately loose. GPS wanders by tens of metres under
    tree cover, and the two sides of a dual carriageway are the same route.

    **Two things here used to be one thing, and that was the bug.** Probes were
    compared against the other track's *resampled vertices*, at a fixed 200 of
    them. Vertex spacing is then ``length / 199`` — 31 m for a 6 km commute but
    493 m for a 98 km ride — and a point lying exactly on the route sits up to
    half a spacing from the nearest vertex. The comparison tolerance therefore
    grew with route length and above roughly 24 km nothing could match: two
    recordings of the same 62 km loop scored 0.365 where the true similarity is
    0.987. Measuring to segments removes the dependency entirely, and the probe
    count now only controls how *thoroughly* each track is checked.

    ``samples`` overrides the derived probe count. Only tests pass it, to pin
    the length-independence property against the old fixed-count behaviour.

    Note this is pure geometry: it does not know which way either track was
    ridden. ``opposite_directions`` is the separate question, and the caller
    decides what to do about it.
    """
    if not a or not b:
        return 0.0

    # One projection plane for both tracks; see _to_local_metres.
    lat_scale = math.cos(math.radians((a[0][0] + b[0][0]) / 2.0))

    probes_a = resample_polyline(a, samples if samples is not None else _probe_count(a))
    probes_b = resample_polyline(b, samples if samples is not None else _probe_count(b))
    forward = _coverage(probes_a, b, tolerance_m, lat_scale)
    backward = _coverage(probes_b, a, tolerance_m, lat_scale)
    return min(forward, backward)


def is_loop(points: list[Point], margin_m: float = LOOP_MARGIN_M) -> bool:
    """Whether a track finishes where it started.

    The distinction matters because direction is only meaningful for a
    point-to-point ride. A loop ridden the other way round is the same route;
    a commute ridden the other way round climbs the hill it used to descend.
    """
    if len(points) < 2:
        return True
    return haversine_m(points[0], points[-1]) <= margin_m


def opposite_directions(a: list[Point], b: list[Point]) -> bool:
    """Whether two point-to-point tracks run the same roads in reverse.

    ``False`` whenever either track is a loop — a loop has no direction worth
    distinguishing, and its endpoints coincide, so the test below would be
    noise.

    For the rest, the endpoints are simply paired up both ways and the closer
    pairing wins. No absolute threshold is involved, so a ride that started a
    few streets early is unaffected, and the answer degrades gracefully rather
    than flipping at a cliff edge.
    """
    if len(a) < 2 or len(b) < 2 or is_loop(a) or is_loop(b):
        return False
    same_way = haversine_m(a[0], b[0]) + haversine_m(a[-1], b[-1])
    reversed_way = haversine_m(a[0], b[-1]) + haversine_m(a[-1], b[0])
    return reversed_way < same_way


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

    ``width_px`` deliberately under-states the real container (which is around
    1090 px: a Radix ``size="4"`` container less the 16 em sidebar). Because
    the result is the *smaller* of the two axis fits, under-stating width can
    only zoom further out — it letterboxes, it never clips. Over-stating it
    would clip. Keep the conservative value.
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


def bbox_overlap(a: list[Point], b: list[Point]) -> float:
    """Overlap of two tracks' bounding boxes, as a fraction of the smaller one.

    A cheap gate in front of :func:`route_match_score`, which costs a few
    hundred milliseconds a pair. Two tracks whose boxes barely intersect cannot
    possibly be the same route, and skipping them turns "score against every
    known route" from seconds into nothing.

    Relative to the *smaller* box on purpose: a short ride wholly inside a long
    one scores 1.0 here and goes on to be scored properly, where dividing by
    the union would reject it before it was ever looked at.
    """
    if not a or not b:
        return 0.0
    a_min_lat, a_min_lon, a_max_lat, a_max_lon = bounding_box(a)
    b_min_lat, b_min_lon, b_max_lat, b_max_lon = bounding_box(b)

    lat_overlap = max(0.0, min(a_max_lat, b_max_lat) - max(a_min_lat, b_min_lat))
    lon_overlap = max(0.0, min(a_max_lon, b_max_lon) - max(a_min_lon, b_min_lon))

    a_area = (a_max_lat - a_min_lat) * (a_max_lon - a_min_lon)
    b_area = (b_max_lat - b_min_lat) * (b_max_lon - b_min_lon)
    smaller = min(a_area, b_area)
    if smaller <= 0.0:
        # A track confined to one line of latitude or longitude has no area.
        # Fall back to "do the boxes touch at all", rather than dividing by 0.
        return 1.0 if lat_overlap > 0.0 or lon_overlap > 0.0 else 0.0
    return (lat_overlap * lon_overlap) / smaller


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
