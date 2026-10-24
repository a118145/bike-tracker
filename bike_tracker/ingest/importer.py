"""The one function every ride source calls.

Local file drops and, later, the Hammerhead poller both end here: a
:class:`~.fit.ParsedRide` goes in, persisted rows come out. Keeping the
sequence in one place is what stops the two sources drifting into two
slightly different definitions of "imported".

The order below is deliberate and each step depends on the one before it:

1. **Bike detection** from the ANT+ device ids in the file.
2. **FTP at the time of the ride**, so step 3 can compute load against the
   right number.
3. **Metrics**, computed from the streams and cross-checked against the FIT
   session's own figures.
4. **Route matching**, which needs the simplified polyline from step 3.
5. **Streams**, compressed into a single row.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from .. import metrics
from ..models import (
    EVAL_FTP,
    EVAL_LTHR,
    Bike,
    BikeSensor,
    FitnessEval,
    Lap,
    MetricFields,
    Ride,
    RideStream,
    Route,
    excluded_ride_ids,
)
from ..utils.logging import get_logger
from . import streams
from .fit import ParsedRide
from .summarize import ChannelWindow, cross_check, summarize

log = get_logger(__name__)

# How closely a ride must follow an existing route to be counted as the same
# one. Re-derived from 88 real rides after `route_match_score` was fixed: the
# old 0.75 was tuned against a scorer whose tolerance grew with route length,
# so its stated reasoning ("above ~0.85 a single road closure spawns a
# duplicate route") no longer holds — a 2 km detour on a 60 km ride now scores
# ~0.97. There is no clean gap in the distribution, because rides out of one
# city genuinely share their first few kilometres; 0.80 sits above the dense
# 0.40-0.72 band of partial overlaps and below the confident matches.
ROUTE_MATCH_THRESHOLD = 0.80

# Gate applied before scoring a candidate route, because a single
# `route_match_score` costs a few hundred milliseconds and an import compares
# against every route there is. Two tracks whose lengths or bounding boxes
# disagree this much cannot be the same route. Measured on the real data:
# keeps 9% of pairs, at most 23 candidates for one ride, and excludes no true
# match.
ROUTE_CANDIDATE_LENGTH_RATIO = 0.75
ROUTE_CANDIDATE_BBOX_OVERLAP = 0.5

# Douglas-Peucker tolerance for the stored route geometry. 15 m keeps every
# junction and bend that distinguishes one route from another while cutting a
# 10,000-point track to a few hundred points.
ROUTE_SIMPLIFY_EPSILON_M = 15.0

# Coarser tolerance for the list-card thumbnail: at ~84 px across, 50 m of
# detail is a fraction of a pixel and only costs bytes in the page payload.
PREVIEW_SIMPLIFY_EPSILON_M = 50.0


@dataclass
class ImportResult:
    """What :func:`import_ride` did, for the caller to report to the user."""

    ride: Ride
    created: bool
    warnings: list[str]


def import_ride(
    parsed: ParsedRide,
    session: Session,
    *,
    name: str | None = None,
    source: str = "file",
    external_id: str | None = None,
) -> ImportResult:
    """Persist a parsed ride, deriving everything it needs on the way in.

    Args:
        parsed: Output of :func:`..ingest.fit.parse`.
        session: An open SQLModel session. The caller owns the transaction.
        name: Display name; falls back to a name generated from the start time.
        source: Which ingest source produced this ("file", "hammerhead", ...).
        external_id: The source's own id, for deduplication across polls.

    Returns:
        The ride, whether it was newly created, and any cross-check warnings.
    """
    existing = session.exec(select(Ride).where(Ride.fit_hash == parsed.fit_hash)).first()
    if existing is not None:
        log.info("ride.import.duplicate", ride_id=existing.id, fit_hash=parsed.fit_hash[:12])
        return ImportResult(ride=existing, created=False, warnings=[])

    bike_id = resolve_bike(parsed, session)
    ftp_w, lthr_bpm = resolve_fitness(session, parsed.end_time, parsed)

    window = ChannelWindow.from_parsed(parsed)
    computed = summarize(window, fit=parsed.session, ftp_w=ftp_w, lthr_bpm=lthr_bpm)
    warnings = cross_check(parsed.session, computed, window)

    ride = Ride(
        name=name or _default_name(parsed.start_time),
        fit_hash=parsed.fit_hash,
        source=source,
        external_id=external_id,
        bike_id=bike_id,
        start_time=parsed.start_time,
        end_time=parsed.end_time,
        sport=parsed.sport,
        num_laps=len(parsed.laps),
        stream_offset_s=_stream_offset_s(parsed),
        # Kept so a later refresh still has the head unit's own numbers to
        # fall back on once the FIT file is gone. See resolve_fitness.
        device_ftp_w=device_value(parsed, "threshold_power"),
        device_lthr_bpm=device_value(parsed, "threshold_heart_rate"),
        **computed.model_dump(),
    )
    session.add(ride)
    session.commit()
    session.refresh(ride)
    assert ride.id is not None  # assigned by the database on commit

    _store_laps(parsed, window, ride.id, session, ftp_w=ftp_w, lthr_bpm=lthr_bpm)
    _store_streams(parsed, ride.id, session)

    track = metrics.to_points(
        metrics.as_float_array(parsed.channels.get("lat", [])),
        metrics.as_float_array(parsed.channels.get("lon", [])),
    )
    if track:
        ride.preview_points = metrics.project_svg_points(
            metrics.simplify_polyline(track, PREVIEW_SIMPLIFY_EPSILON_M)
        )
        ride.map_points = simplified_track(track)
        assign_route(ride, track, session)

    if bike_id is not None:
        _add_to_odometer(session, bike_id, ride.distance_m)

    session.commit()
    session.refresh(ride)

    log.info(
        "ride.import",
        ride_id=ride.id,
        name=ride.name,
        bike_id=ride.bike_id,
        route_id=ride.route_id,
        distance_km=round(ride.distance_m / 1000, 1),
        np_w=ride.normalized_power_w,
        tss=ride.tss,
        warnings=len(warnings),
    )
    return ImportResult(ride=ride, created=True, warnings=warnings)


# ---------------------------------------------------------------------------
# 1. Bike detection
# ---------------------------------------------------------------------------


def resolve_bike(parsed: ParsedRide, session: Session) -> int | None:
    """Which bike this ride was ridden on, from the paired ANT+ sensors.

    Sensor ids claimed by more than one bike are ignored. That is not a corner
    case here: the same Decathlon chest strap is used on both bikes (brief
    §2), and it does appear in both reference files under ANT+ id 44050 —
    matching on it would make every ride ambiguous. Power meters are unique
    per bike, which is exactly why the brief chose ANT+ id over "has a power
    meter" as the discriminator.

    Returns None when nothing matches, leaving the ride unassigned for the
    ride-detail dropdown to fix by hand.
    """
    ant_ids = parsed.ant_ids
    if not ant_ids:
        return None

    sensors = session.exec(select(BikeSensor).where(col(BikeSensor.ant_id).in_(ant_ids))).all()
    if not sensors:
        log.info("ride.import.bike.unmatched", ant_ids=ant_ids)
        return None

    owners_per_ant: dict[int, set[int]] = {}
    for sensor in sensors:
        owners_per_ant.setdefault(sensor.ant_id, set()).add(sensor.bike_id)

    votes: Counter[int] = Counter()
    for ant_id, owners in owners_per_ant.items():
        if len(owners) != 1:
            log.debug("ride.import.bike.ambiguous_sensor", ant_id=ant_id, bikes=sorted(owners))
            continue
        votes[next(iter(owners))] += 1

    if not votes:
        log.info("ride.import.bike.only_shared_sensors", ant_ids=ant_ids)
        return None

    ranked = votes.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        log.warning("ride.import.bike.tie", candidates=[b for b, _ in ranked])
        return None
    return ranked[0][0]


# ---------------------------------------------------------------------------
# 2. FTP at the time of the ride
# ---------------------------------------------------------------------------


def resolve_fitness(
    session: Session,
    ride_end: datetime,
    parsed: ParsedRide | None = None,
    *,
    device_ftp_w: int | None = None,
    device_lthr_bpm: int | None = None,
) -> tuple[int | None, int | None]:
    """The FTP and LTHR that applied when this ride happened.

    One chain, used by both callers — import and every later refresh — so the
    two cannot develop different ideas of the order:

    1. the newest evaluation with ``valid_from`` at or before ``ride_end``.
       Masterplan §2.4.2 is explicit that it is the last value *before* the
       ride, not the newest overall: taking the newest would rewrite the
       training load of every past ride the next time FTP is raised, which is
       what ``Ride.ftp_at_time_w`` exists to prevent (brief §5.2);
    2. what the head unit itself was configured with — passed as
       ``device_ftp_w`` from the stored column on a refresh, or read off
       ``parsed`` at import, where the row does not exist yet;
    3. nothing.

    Step 2 is why ``Ride.device_ftp_w`` is stored at all. Without it, deleting
    the last evaluation leaves the ride quoting that deleted evaluation's
    number, backed by nothing.

    There is no step 2 for LTHR in practice: the Karoo writes no threshold
    heart rate. ``max_heart_rate`` is a different quantity and is deliberately
    not substituted.

    Args:
        ride_end: Last timestamp of the ride.
        parsed: The freshly parsed file, on the import path only.
        device_ftp_w: The stored head-unit FTP, on the refresh path.
        device_lthr_bpm: The stored head-unit LTHR, on the refresh path.
    """
    ftp = _latest_eval_value(session, EVAL_FTP, ride_end)
    lthr = _latest_eval_value(session, EVAL_LTHR, ride_end)

    if ftp is None:
        ftp = device_ftp_w if device_ftp_w is not None else device_value(parsed, "threshold_power")
        if ftp is not None:
            log.info("ride.ftp.from_device", ftp_w=ftp)
    if lthr is None:
        lthr = (
            device_lthr_bpm
            if device_lthr_bpm is not None
            else device_value(parsed, "threshold_heart_rate")
        )

    return ftp, lthr


def device_value(parsed: ParsedRide | None, field: str) -> int | None:
    """A threshold the head unit recorded in the FIT session, if any.

    Zero counts as absent: an unconfigured head unit writes 0, and scoring a
    ride against an FTP of zero is worse than scoring it against none.
    """
    if parsed is None:
        return None
    raw = parsed.session.get(field)
    return int(raw) if raw else None


def _latest_eval_value(session: Session, eval_type: str, before: datetime) -> int | None:
    statement = (
        select(FitnessEval)
        .where(FitnessEval.type == eval_type)
        .where(col(FitnessEval.valid_from) <= before)
        .order_by(col(FitnessEval.valid_from).desc())
    )
    found = session.exec(statement).first()
    return round(found.value) if found is not None else None


# ---------------------------------------------------------------------------
# 3. Metrics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. Route matching
# ---------------------------------------------------------------------------


# Coordinate precision for stored geometry. Five decimals is ~1.1 m, well
# under the 15 m the polyline is simplified to, and it nearly halves the JSON.
COORD_DECIMALS = 5


def simplified_track(track: list[metrics.Point]) -> list[list[float]]:
    """A ride's track, reduced to what a map actually needs.

    Douglas-Peucker at :data:`ROUTE_SIMPLIFY_EPSILON_M` keeps the drawn line
    within 15 m of the truth — far tighter than the 60 m tolerance
    ``route_match_score`` uses, so the deviations the route heat map exists to
    show survive the simplification.
    """
    return [
        [round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)]
        for lat, lon in metrics.simplify_polyline(track, ROUTE_SIMPLIFY_EPSILON_M)
    ]


def ensure_map_points(session: Session, rides: Sequence[Ride]) -> int:
    """Backfill :attr:`Ride.map_points` for rides imported before it existed.

    Rebuilds the polyline from the stored streams — exact, because the value is
    pure geometry with nothing copied from the device. Returns how many rides
    were filled. Rides with no GPS track are left with an empty list and are
    not retried, since there is nothing to compute.
    """
    filled = 0
    for ride in rides:
        if ride.map_points:
            continue
        stream = session.exec(select(RideStream).where(RideStream.ride_id == ride.id)).first()
        if stream is None:
            continue
        track = metrics.to_points(streams.decode(stream.lat), streams.decode(stream.lon))
        if not track:
            continue
        ride.map_points = simplified_track(track)
        session.add(ride)
        filled += 1
    if filled:
        session.commit()
        log.info("ride.map_points.backfilled", rides=filled)
    return filled


def assign_route(ride: Ride, track: list[metrics.Point], session: Session) -> Route:
    """Attach the ride to the best-matching route, or start a new one."""
    simplified = metrics.simplify_polyline(track, ROUTE_SIMPLIFY_EPSILON_M)
    ride_length_m = metrics.polyline_length_m(simplified)

    best_route: Route | None = None
    best_score = 0.0
    for route in session.exec(select(Route)).all():
        if not route.points:
            continue
        candidate = [(p[0], p[1]) for p in route.points]

        # Cheap rejections first. Scoring is the expensive part, and most
        # routes in a growing table are nowhere near a given ride.
        candidate_length_m = metrics.polyline_length_m(candidate)
        longest = max(ride_length_m, candidate_length_m)
        if longest <= 0.0:
            continue
        if min(ride_length_m, candidate_length_m) / longest < ROUTE_CANDIDATE_LENGTH_RATIO:
            continue
        if metrics.bbox_overlap(simplified, candidate) < ROUTE_CANDIDATE_BBOX_OVERLAP:
            continue

        # A point-to-point ride done in reverse is a different route: the
        # commute that climbs the hill on the way out descends it on the way
        # back, and averaging the two would describe neither. Loops have no
        # direction worth distinguishing, and `opposite_directions` says so.
        if metrics.opposite_directions(simplified, candidate):
            continue

        score = metrics.route_match_score(simplified, candidate)
        if score > best_score:
            best_route, best_score = route, score

    if best_route is not None and best_score >= ROUTE_MATCH_THRESHOLD:
        ride.route_id = best_route.id
        log.info(
            "ride.route.matched",
            ride_id=ride.id,
            route_id=best_route.id,
            score=round(best_score, 3),
        )
        route = best_route
    else:
        route = Route(
            name=_unique_route_name(session, ride.name), points=[list(p) for p in simplified]
        )
        session.add(route)
        session.commit()
        session.refresh(route)
        ride.route_id = route.id
        log.info(
            "ride.route.created",
            ride_id=ride.id,
            route_id=route.id,
            best_score=round(best_score, 3),
        )

    session.add(ride)
    session.commit()
    refresh_route_stats(session, route)
    return route


def refresh_route_stats(session: Session, route: Route) -> Route | None:
    """Recompute a route's averages, deleting it if nothing rides it any more.

    A route is derived data: it exists because rides matched it, so a route
    with no rides is not an empty route but a route that should not exist. Left
    behind it would clutter the routes list and the ride-detail dropdown
    forever, and selecting it there would write a foreign key to a row nobody
    can reach.

    The pruning lives here rather than in the caller because this is the one
    choke point every count change passes through — it is called on *both*
    sides of a reassignment. A hook in the state would work today only because
    nothing else moves rides yet.

    **Never call this before the ride's new ``route_id`` is committed.**
    ``assign_route`` is safe: it commits the ride first, so a route it has just
    created always has at least one ride and cannot be deleted out from under
    the caller.

    Emptiness is judged on **every** ride, the statistics only on rides in
    analysis. Pruning on the analysed count would delete a route the moment
    its last ride was excluded, and restoring that ride could not bring it
    back. A route whose rides are all excluded therefore stays, with a count
    of zero and no averages.

    Returns the route, or ``None`` when it was pruned — so a caller cannot go
    on holding a row that no longer exists.
    """
    rides = session.exec(select(Ride).where(Ride.route_id == route.id)).all()

    if not rides:
        route_id, name = route.id, route.name
        session.delete(route)
        session.commit()
        log.info("route.pruned", route_id=route_id, name=name)
        return None

    excluded = excluded_ride_ids(session, (r.id for r in rides if r.id is not None))
    counted = [r for r in rides if r.id not in excluded]
    route.ride_count = len(counted)
    route.avg_distance_m = _mean([r.distance_m for r in counted])
    route.avg_elevation_gain_m = _mean([r.elevation_gain_m for r in counted])
    route.avg_moving_time_s = _mean([r.moving_time_s for r in counted])
    session.add(route)
    session.commit()
    return route


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def refresh_routes_for_rides(session: Session, ride_ids: Iterable[int]) -> int:
    """Refresh the statistics of every route these rides sit on.

    Called whenever the set of excluded rides changes — an excluding tag put
    on or taken off a ride, a tag's effect switched, an excluding tag deleted
    (AGENTS.md rule 43). Each route is refreshed once, however many of the
    rides share it. Returns the number of routes refreshed.
    """
    ids = list(ride_ids)
    if not ids:
        return 0
    route_ids = set(
        session.exec(
            select(Ride.route_id).where(col(Ride.id).in_(ids), col(Ride.route_id).is_not(None))
        ).all()
    )
    for route_id in route_ids:
        route = session.get(Route, route_id)
        if route is not None:
            refresh_route_stats(session, route)
    return len(route_ids)


def create_manual_route(session: Session, ride: Ride, base_name: str) -> Route:
    """Start a new route from one ride, because the matcher got it wrong.

    The counterpart to reassignment: when the 0.75 threshold has lumped two
    genuinely different rides together, this splits one off.

    The new route takes **the ride's own polyline** as its geometry. That is
    not a detail — ``assign_route`` skips routes whose ``points`` are empty, so
    a route created without geometry could never gain a second ride and would
    be a permanent dead end.

    Placeholder names are numbered plainly ("New route 2"), unlike the
    ``X (2)`` that :func:`_unique_route_name` produces for auto-created
    routes: these are placeholders awaiting a name, not disambiguated ones.
    """
    route = Route(
        name=_numbered_name(session, base_name),
        points=[list(p) for p in ride.map_points],
    )
    session.add(route)
    session.commit()
    session.refresh(route)

    ride.route_id = route.id
    session.add(ride)
    session.commit()

    log.info("route.created_manually", route_id=route.id, ride_id=ride.id)
    return route


def _numbered_name(session: Session, base: str) -> str:
    """``base``, ``base 2``, ``base 3`` — the first that is free."""
    taken = {r.name for r in session.exec(select(Route)).all()}
    if base not in taken:
        return base
    suffix = 2
    while f"{base} {suffix}" in taken:
        suffix += 1
    return f"{base} {suffix}"


def _unique_route_name(session: Session, seed: str) -> str:
    base = seed.strip() or "Route"
    existing = {r.name for r in session.exec(select(Route)).all()}
    if base not in existing:
        return base
    suffix = 2
    while f"{base} ({suffix})" in existing:
        suffix += 1
    return f"{base} ({suffix})"


# ---------------------------------------------------------------------------
# 5. Laps and streams
# ---------------------------------------------------------------------------


def _store_laps(
    parsed: ParsedRide,
    window: ChannelWindow,
    ride_id: int,
    session: Session,
    *,
    ftp_w: int | None,
    lthr_bpm: int | None,
) -> None:
    """Persist one fully-populated row per lap.

    Each lap is summarized over its own slice of the ride's streams, so a lap
    carries the same metrics a ride does rather than the handful the FIT lap
    message happens to include. ``cross_check`` is deliberately not run here —
    see its docstring.
    """
    origin = _stream_offset_s(parsed)
    for parsed_lap in parsed.laps:
        start_s = (parsed_lap.start_time - parsed.start_time).total_seconds() - origin
        end_s = (parsed_lap.end_time - parsed.start_time).total_seconds() - origin
        i0, i1 = metrics.window_indices(window.time_s, start_s, end_s)

        computed = summarize(
            window.slice(i0, i1),
            fit=parsed_lap.fields,
            ftp_w=ftp_w,
            lthr_bpm=lthr_bpm,
        )
        session.add(
            Lap(
                ride_id=ride_id,
                index=parsed_lap.index,
                start_time=parsed_lap.start_time,
                end_time=parsed_lap.end_time,
                **computed.model_dump(),
            )
        )
        log.debug(
            "ride.import.lap",
            ride_id=ride_id,
            index=parsed_lap.index,
            samples=i1 - i0,
            distance_km=round(computed.distance_m / 1000, 2),
        )
    session.commit()


def _store_streams(parsed: ParsedRide, ride_id: int, session: Session) -> None:
    row = RideStream(ride_id=ride_id, sample_count=parsed.sample_count)
    row.time_s = streams.encode(parsed.time_offsets_s())
    for name in streams.STREAM_CHANNELS:
        if name == "time_s":
            continue
        setattr(row, name, streams.encode(parsed.channels.get(name)))
    session.add(row)
    session.commit()


def recalculate_odometer(session: Session, bike_id: int) -> float:
    """Rebuild a bike's odometer from the rides currently assigned to it.

    The incremental updates in :func:`_add_to_odometer` and
    ``RideDetailState.set_bike`` are O(1) but they drift: reassigning a ride
    subtracts with a ``max(0.0, ...)`` clamp, so a sequence of edits can leave
    the total short. This is the repair, offered from the bike's edit dialog
    exactly as masterplan §3.1 specifies.

    ``coalesce`` is not decoration: a bike with no rides makes ``SUM`` return
    NULL, and ``odo_m`` is not nullable.
    """
    bike = session.get(Bike, bike_id)
    if bike is None:
        return 0.0

    total = session.exec(
        select(func.coalesce(func.sum(col(Ride.distance_m)), 0.0)).where(
            col(Ride.bike_id) == bike_id
        )
    ).one()

    bike.odo_m = float(total)
    session.add(bike)
    session.commit()
    log.info("bike.odometer.recalculated", bike_id=bike_id, odo_km=round(bike.odo_m / 1000, 1))
    return bike.odo_m


def refresh_ftp_dependent(session: Session, since: datetime) -> int:
    """Re-resolve FTP and recompute the values that depend on it.

    The single choke point for every change to the evaluation history —
    create, edit and delete all call it, so no path can leave a ride quoting a
    superseded FTP.

    **Why every ride from ``since`` forward, not just the evaluated one.**
    Masterplan §2.4.2 only mentions the ride the evaluation came from, but a
    new evaluation changes what "the last value before the ride" resolves to
    for every *later* ride too, up to the next evaluation. Recomputing only
    the one ride leaves two rides a week apart disagreeing about the same
    FTP, and brief §5.2's whole point is that the number a ride was scored
    against must stay consistent.

    Earlier rides are deliberately untouched: rewriting them is exactly the
    retroactive drift ``ftp_at_time_w`` exists to prevent.

    Only the three FTP-dependent fields are rewritten, from the stored
    normalized power and moving time. A full ``summarize`` over the stored
    streams (``ChannelWindow.from_stream``) would recompute everything the
    device wrote into the FIT session — distance, ascent, timer time — without
    that message, because the file is not kept (§1.3), and so drift from the
    import-time values. Nothing else in the metric set depends on FTP, so
    there is nothing else to redo.

    Returns:
        How many rides were updated.
    """
    rides = session.exec(
        select(Ride).where(col(Ride.start_time) >= since).order_by(col(Ride.start_time))
    ).all()

    updated = 0
    for ride in rides:
        # Same chain as import: evaluation, else what the head unit said, else
        # nothing. Deliberately *not* "keep whatever the column holds" — that
        # would leave a deleted evaluation's number in place, backed by
        # nothing, which is the bug this replaced.
        ftp_w, lthr_bpm = resolve_fitness(
            session,
            ride.end_time,
            device_ftp_w=ride.device_ftp_w,
            device_lthr_bpm=ride.device_lthr_bpm,
        )
        laps = session.exec(select(Lap).where(Lap.ride_id == ride.id)).all()
        for target in (ride, *laps):
            _apply_load(target, ftp_w, lthr_bpm)
            session.add(target)
        updated += 1

    session.commit()
    log.info("fitness.refresh", since=since.isoformat(), rides=updated)
    return updated


def _apply_load(target: MetricFields, ftp_w: int | None, lthr_bpm: int | None) -> None:
    """Rewrite intensity factor and TSS for one ride or lap.

    A lap repeats its parent ride's FTP by design (``MetricFields``), which is
    what lets a selected lap fill the same stat cards — so laps are refreshed
    with the ride rather than being left behind.
    """
    target.ftp_at_time_w = ftp_w
    target.lthr_at_time_bpm = lthr_bpm

    np_w = target.normalized_power_w or 0.0
    if target.power_available and ftp_w and np_w > 0:
        if_ = metrics.intensity_factor(np_w, ftp_w)
        target.intensity_factor = if_
        target.tss = metrics.tss(np_w, if_, target.moving_time_s)
        return

    # No power, or no FTP to score against: fall back to the heart-rate load
    # exactly as import does, so the two paths cannot diverge.
    target.intensity_factor = None
    target.tss = None
    if lthr_bpm and target.avg_hr_bpm:
        ratio = target.avg_hr_bpm / lthr_bpm
        target.tss = target.moving_time_s / 3600.0 * ratio**2 * 100.0


def _add_to_odometer(session: Session, bike_id: int, distance_m: float) -> None:
    """Incremental odometer update, per masterplan §3.1.

    Adding this ride's distance is O(1); recomputing from every ride the bike
    has ever done is O(n) on each import. A full recalculation is available
    from the bike's edit dialog for when the incremental value drifts.
    """
    bike = session.get(Bike, bike_id)
    if bike is None:
        return
    bike.odo_m += distance_m
    session.add(bike)
    session.commit()


# ---------------------------------------------------------------------------
# Small conversions
# ---------------------------------------------------------------------------


def _stream_offset_s(parsed: ParsedRide) -> float:
    """Seconds between the session start and the first record.

    ``RideStream.time_s`` has its origin at the first *record*, while
    ``Ride.start_time`` is the session start. They differ by two seconds on the
    evening reference ride, which is enough to shift every lap window by two
    samples if the two origins are used interchangeably.
    """
    if not parsed.timestamps:
        return 0.0
    return (parsed.timestamps[0] - parsed.start_time).total_seconds()


def _default_name(start: datetime) -> str:
    """A name for rides whose source gave none, e.g. "Morning Ride"."""
    hour = start.hour
    if hour < 12:
        part = "Morning"
    elif hour < 17:
        part = "Afternoon"
    elif hour < 21:
        part = "Evening"
    else:
        part = "Night"
    return f"{part} Ride"
