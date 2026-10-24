"""Database rows → API responses, through the helpers the pages use.

AGENTS.md rule 45: the API serves what the app shows. Every function here
reads the same stored values a page renders and goes through the same shared
helpers — ``analysis_rides``, ``latest_evaluations``, ``power_curve_windows``,
``route_summary``, ``last_ridden_by_route``, ``lap_window_s``,
``load_ride_tags``, ``load_note_summaries``, ``excluded_ride_ids``,
``group_evaluations``. A number that would need new maths here belongs in
``MetricFields`` and on a page first.

The one on-the-fly computation is the window cutter, and it runs
``summarize`` — the function that produced every Ride and Lap row — over a
slice of the stored streams. Lists only read stored scalars (rule 11); only
the window and streams endpoints decode a ride's streams, one ride at a time,
as the ride page does.
"""

import json
import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from .. import metrics
from ..components.ride_chart import CHANNEL_SPECS
from ..ingest import streams
from ..ingest.summarize import ChannelWindow, summarize
from ..metrics import FloatArray
from ..models import (
    CURVE_WINDOW_DAYS,
    EVAL_BASELINE,
    EVAL_FTP,
    EVAL_LTHR,
    Bike,
    BikeSensor,
    FitnessEval,
    Lap,
    MetricFields,
    Ride,
    RideNote,
    RideStream,
    RideTag,
    Route,
    analysis_rides,
    excluded_ride_ids,
    lap_window_s,
    last_ridden_by_route,
    latest_evaluations,
    load_note_summaries,
    power_curve_windows,
    route_summary,
)
from ..states.rides_state import group_evaluations
from ..states.tags_state import all_tags, find_tag_by_name, load_ride_tags
from ..utils.local_time import app_timezone, to_local
from . import schemas

API_VERSION = "v1"

# The connector's tool-result limit is ~150,000 characters (brief §3.4); the
# streams endpoint stays under this with room for the MCP envelope.
STREAM_BUDGET_CHARS = 120_000
MAX_STREAM_WINDOW_S = 3600

# What the ride page's chart can draw: its toggleable channels plus the
# elevation base trace. Named by their RideStream columns, i.e. with units.
STREAM_CHANNEL_NAMES: tuple[str, ...] = (
    "altitude_m",
    *(column for column, _label, _unit, _colour in CHANNEL_SPECS.values()),
)

# Fixed English names: `strftime("%A")` follows the process locale.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

EVAL_UNITS = {EVAL_FTP: "W", EVAL_LTHR: "bpm", EVAL_BASELINE: "W/bpm"}


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": f"{what} not found."})


def _invalid(message: str, **extra: object) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"error": message, **extra})


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _when(value: datetime) -> dict[str, Any]:
    """``start_time_utc`` plus the local calendar fields, from one timestamp."""
    local = to_local(value)
    return {
        "start_time_utc": _utc(value),
        "local_date": local.date(),
        "local_start_time": local.strftime("%H:%M"),
        "weekday": WEEKDAYS[local.weekday()],
    }


def _utc_bound(day: date) -> datetime:
    """Local midnight at the start of ``day``, as a stored (naive UTC) timestamp."""
    local_midnight = datetime.combine(day, time.min, tzinfo=app_timezone())
    return local_midnight.astimezone(UTC).replace(tzinfo=None)


def _metrics(source: MetricFields) -> dict[str, Any]:
    return {name: getattr(source, name) for name in MetricFields.model_fields}


def _bike_names(session: Session) -> dict[int, str]:
    return {b.id: b.name for b in session.exec(select(Bike)).all() if b.id is not None}


def _route_names(session: Session) -> dict[int, str]:
    return {r.id: r.name for r in session.exec(select(Route)).all() if r.id is not None}


def _evaluation(record: FitnessEval) -> schemas.EvaluationOut:
    return schemas.EvaluationOut(
        id=record.id or 0,
        type=record.type,
        value=record.value,
        unit=EVAL_UNITS.get(record.type, ""),
        valid_from_utc=_utc(record.valid_from),
        local_date=to_local(record.valid_from).date(),
        ride_id=record.ride_id,
        method=record.method,
        # The Fitness page's "From a ride" / "Entered by hand".
        source="ride" if record.ride_id else "manual",
    )


# --- version -----------------------------------------------------------------


def version(session: Session, app_version: str) -> schemas.VersionOut:
    try:
        revision = (
            session.connection()
            .execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one_or_none()
        )
    except SQLAlchemyError:
        revision = None
    return schemas.VersionOut(
        app_version=app_version,
        api_version=API_VERSION,
        schema_revision=revision,
        timezone=str(app_timezone()),
    )


# --- athlete -----------------------------------------------------------------


def athlete(session: Session) -> schemas.AthleteOut:
    """Fitness page header and zone tables, Settings bikes and tags."""
    latest = latest_evaluations(session.exec(select(FitnessEval)).all())
    ftp = latest.get(EVAL_FTP)
    lthr = latest.get(EVAL_LTHR)
    base = latest.get(EVAL_BASELINE)

    sensors: dict[int, list[schemas.SensorOut]] = {}
    for sensor in session.exec(select(BikeSensor).order_by(col(BikeSensor.id))).all():
        sensors.setdefault(sensor.bike_id, []).append(
            schemas.SensorOut(
                sensor_type=sensor.sensor_type, ant_id=sensor.ant_id, label=sensor.label
            )
        )
    bikes = [
        schemas.BikeOut(
            id=bike.id or 0,
            name=bike.name,
            weight_kg=bike.weight_kg,
            odo_m=bike.odo_m,
            sensors=sensors.get(bike.id or 0, []),
        )
        for bike in session.exec(select(Bike).order_by(col(Bike.name))).all()
    ]

    return schemas.AthleteOut(
        ftp_w=ftp.value if ftp else None,
        ftp_valid_from=to_local(ftp.valid_from).date() if ftp else None,
        lthr_bpm=lthr.value if lthr else None,
        lthr_valid_from=to_local(lthr.valid_from).date() if lthr else None,
        baseline_w_per_bpm=base.value if base else None,
        baseline_valid_from=to_local(base.valid_from).date() if base else None,
        power_zones=[
            schemas.PowerZoneOut(
                name=zone.name, description=zone.description, low_w=zone.low, high_w=zone.high
            )
            for zone in metrics.power_zones(ftp.value if ftp else 0.0)
        ],
        hr_zones=[
            schemas.HrZoneOut(
                name=zone.name, description=zone.description, low_bpm=zone.low, high_bpm=zone.high
            )
            for zone in metrics.hr_zones(lthr.value if lthr else 0.0)
        ],
        bikes=bikes,
        tags=[
            schemas.TagOut(id=tag.id or 0, name=tag.name, color=tag.color, effect=tag.effect)
            for tag in all_tags(session)
        ],
    )


# --- activities --------------------------------------------------------------


def list_activities(
    session: Session,
    *,
    from_date: date | None,
    to_date: date | None,
    bike_id: int | None,
    route_id: int | None,
    tag: str | None,
    include_excluded: bool,
    limit: int,
) -> schemas.ActivitiesOut:
    query = select(Ride) if include_excluded else analysis_rides()
    if from_date is not None:
        query = query.where(col(Ride.start_time) >= _utc_bound(from_date))
    if to_date is not None:
        query = query.where(col(Ride.start_time) < _utc_bound(to_date + timedelta(days=1)))
    if bike_id is not None:
        query = query.where(Ride.bike_id == bike_id)
    if route_id is not None:
        query = query.where(Ride.route_id == route_id)
    if tag:
        found = find_tag_by_name(session, tag)
        if found is None:
            known = [t.name for t in all_tags(session)]
            raise _invalid(f"No tag named {tag!r}.", known_tags=known)
        query = query.where(
            col(Ride.id).in_(select(RideTag.ride_id).where(RideTag.tag_id == found.id))
        )

    rides = session.exec(query.order_by(col(Ride.start_time).desc()).limit(limit)).all()
    ids = [ride.id for ride in rides if ride.id is not None]
    bikes = _bike_names(session)
    routes = _route_names(session)
    tags = load_ride_tags(session, ids)
    notes = load_note_summaries(session, ids)
    excluded = excluded_ride_ids(session, ids)
    badges = group_evaluations(
        session.exec(
            select(FitnessEval)
            .where(col(FitnessEval.ride_id).in_(ids))
            .order_by(col(FitnessEval.created_at))
        ).all()
    )

    rows = [
        schemas.ActivityRow(
            id=ride.id or 0,
            name=ride.name,
            **_when(ride.start_time),
            bike_id=ride.bike_id,
            bike_name=bikes.get(ride.bike_id) if ride.bike_id is not None else None,
            route_id=ride.route_id,
            route_name=routes.get(ride.route_id) if ride.route_id is not None else None,
            distance_m=ride.distance_m,
            moving_time_s=ride.moving_time_s,
            elevation_gain_m=ride.elevation_gain_m,
            avg_power_w=ride.avg_power_w,
            normalized_power_w=ride.normalized_power_w,
            avg_hr_bpm=ride.avg_hr_bpm,
            avg_cadence_rpm=ride.avg_cadence_rpm,
            intensity_factor=ride.intensity_factor,
            tss=ride.tss,
            load_source=ride.load_source,
            ftp_at_time_w=ride.ftp_at_time_w,
            decoupling_pct=ride.decoupling_pct,
            num_laps=ride.num_laps,
            evaluations=[badge.label for badge in badges.get(ride.id or 0, [])],
            tags=[pill.name for pill in tags.get(ride.id or 0, [])],
            excluded=ride.id in excluded,
            note_summary=notes.get(ride.id or 0),
        )
        for ride in rides
    ]
    return schemas.ActivitiesOut(
        from_date=from_date,
        to_date=to_date,
        bike_id=bike_id,
        route_id=route_id,
        tag=tag,
        include_excluded=include_excluded,
        limit=limit,
        count=len(rows),
        activities=rows,
    )


def _ride_or_404(session: Session, ride_id: int) -> Ride:
    ride = session.get(Ride, ride_id)
    if ride is None:
        raise _not_found(f"Activity {ride_id}")
    return ride


def _stream(session: Session, ride_id: int) -> RideStream | None:
    return session.exec(select(RideStream).where(RideStream.ride_id == ride_id)).first()


def _laps(session: Session, ride_id: int) -> Sequence[Lap]:
    return session.exec(select(Lap).where(Lap.ride_id == ride_id).order_by(col(Lap.index))).all()


def _recorded_channels(stream: RideStream | None) -> list[str]:
    """Chart channels with at least one real sample — the ride page's test."""
    if stream is None:
        return []
    found = []
    for name in STREAM_CHANNEL_NAMES:
        values = streams.decode(getattr(stream, name, None))
        if values.size and bool(np.isfinite(values).any()):
            found.append(name)
    return found


def activity(session: Session, ride_id: int) -> schemas.ActivityOut:
    """The ride detail page: stat cards, laps, note, tags, evaluations."""
    ride = _ride_or_404(session, ride_id)
    note = session.exec(select(RideNote).where(RideNote.ride_id == ride_id)).first()
    evaluations = session.exec(
        select(FitnessEval)
        .where(FitnessEval.ride_id == ride_id)
        .order_by(col(FitnessEval.created_at))
    ).all()
    bike = session.get(Bike, ride.bike_id) if ride.bike_id is not None else None
    route = session.get(Route, ride.route_id) if ride.route_id is not None else None

    laps = []
    for lap in _laps(session, ride_id):
        window_start_s, window_end_s = lap_window_s(ride, lap)
        local = to_local(lap.start_time)
        laps.append(
            schemas.LapOut.model_validate(
                {
                    **_metrics(lap),
                    "index": lap.index,
                    "start_time_utc": _utc(lap.start_time),
                    "local_start_time": local.strftime("%H:%M:%S"),
                    "window_start_s": window_start_s,
                    "window_end_s": window_end_s,
                }
            )
        )

    return schemas.ActivityOut.model_validate(
        {
            **_metrics(ride),
            **_when(ride.start_time),
            "id": ride_id,
            "name": ride.name,
            "sport": ride.sport,
            "bike_id": ride.bike_id,
            "bike_name": bike.name if bike else None,
            "route_id": ride.route_id,
            "route_name": route.name if route else None,
            "num_laps": ride.num_laps,
            "tags": [pill.name for pill in load_ride_tags(session, [ride_id]).get(ride_id, [])],
            "excluded": bool(excluded_ride_ids(session, [ride_id])),
            "note": (
                schemas.NoteOut(
                    text=note.text,
                    sleep_quality=note.sleep_quality,
                    rpe=note.rpe,
                    perceived_breathing_limit=note.perceived_breathing_limit,
                )
                if note
                else None
            ),
            "evaluations": [_evaluation(record) for record in evaluations],
            "stream_channels": _recorded_channels(_stream(session, ride_id)),
            "laps": laps,
        }
    )


def _has_samples(values: FloatArray) -> bool:
    return bool(values.size) and bool(np.isfinite(values).any())


def window(
    session: Session,
    ride_id: int,
    *,
    lap_index: int | None,
    start_s: float | None,
    end_s: float | None,
    skip_head_s: float,
    skip_tail_s: float,
) -> schemas.WindowOut:
    """Every metric over a slice — ``summarize`` over the stored streams."""
    ride = _ride_or_404(session, ride_id)
    stream = _stream(session, ride_id)
    if stream is None:
        raise _not_found(f"Streams of activity {ride_id}")

    if lap_index is not None:
        laps = list(_laps(session, ride_id))
        lap = next((lap for lap in laps if lap.index == lap_index), None)
        if lap is None:
            raise _invalid(
                f"Activity {ride_id} has no lap with index {lap_index}.",
                lap_indices=[lap.index for lap in laps],
            )
        lo, hi = lap_window_s(ride, lap)
    elif start_s is not None and end_s is not None:
        lo, hi = start_s, end_s
    else:
        raise _invalid("Give either lap_index, or both start_s and end_s.")

    lo += skip_head_s
    hi -= skip_tail_s
    channels = ChannelWindow.from_stream(stream)
    i0, i1 = metrics.window_indices(channels.time_s, lo, hi)
    if i1 - i0 < 2:
        raise _invalid(
            "The window holds fewer than two samples.",
            start_s=lo,
            end_s=hi,
            ride_span_s=channels.span_s,
        )

    cut = channels.slice(i0, i1)
    computed = summarize(cut, ftp_w=ride.ftp_at_time_w, lthr_bpm=ride.lthr_at_time_bpm)
    power = cut.get("power_w")
    hr = cut.get("hr_bpm")
    has_power = _has_samples(power)
    return schemas.WindowOut.model_validate(
        {
            **_metrics(computed),
            "activity_id": ride_id,
            "lap_index": lap_index,
            "start_s": float(cut.time_s[0]),
            "end_s": float(cut.time_s[-1]),
            "sample_count": i1 - i0,
            "ftp_estimate_w": metrics.ftp_from_window(power) if has_power else None,
            "baseline_w_per_bpm": (
                metrics.baseline(power, hr) if has_power and _has_samples(hr) else None
            ),
        }
    )


def stream_window(
    session: Session,
    ride_id: int,
    *,
    start_s: float,
    end_s: float,
    step_s: int,
    channels: Sequence[str],
) -> schemas.StreamsOut:
    """Raw samples for a bounded window, block-averaged to ``step_s``.

    Over the character budget this refuses with a smaller-window suggestion
    rather than truncating (brief §5.6): a silently shortened stream reads as
    the end of the effort.
    """
    unknown = [name for name in channels if name not in STREAM_CHANNEL_NAMES]
    if unknown:
        raise _invalid(
            f"Unknown channel(s): {', '.join(unknown)}.", known=list(STREAM_CHANNEL_NAMES)
        )
    if end_s <= start_s:
        raise _invalid("end_s must be greater than start_s.")
    if end_s - start_s > MAX_STREAM_WINDOW_S:
        raise _invalid(
            f"The window may span at most {MAX_STREAM_WINDOW_S} s.",
            max_window_s=MAX_STREAM_WINDOW_S,
        )

    _ride_or_404(session, ride_id)
    stream = _stream(session, ride_id)
    if stream is None:
        raise _not_found(f"Streams of activity {ride_id}")

    time_all = streams.decode(stream.time_s)
    i0, i1 = metrics.window_indices(time_all, start_s, end_s)
    # Half-open [start_s, end_s): a sample exactly on end_s would open one
    # extra block of a single sample.
    while i1 > i0 and time_all[i1 - 1] >= end_s:
        i1 -= 1
    time_s = time_all[i0:i1]
    bins = np.floor((time_s - start_s) / step_s).astype(np.int64)
    keys, inverse = np.unique(bins, return_inverse=True)

    def block_means(values: FloatArray) -> list[float | None]:
        finite = np.isfinite(values)
        sums = np.bincount(inverse, weights=np.where(finite, values, 0.0), minlength=keys.size)
        counts = np.bincount(inverse, weights=finite.astype(np.float64), minlength=keys.size)
        # NaN, never 0, is a missing sample (rule 13) — and JSON spells it null.
        return [
            round(float(s / c), 2) if c > 0 else None for s, c in zip(sums, counts, strict=True)
        ]

    out_channels: dict[str, list[float | None]] = {}
    for name in channels:
        values = streams.decode(getattr(stream, name, None))
        # A channel the ride never recorded comes back empty, not as a row of
        # nulls the length of the window.
        out_channels[name] = block_means(values[i0:i1]) if values.size else []

    result = schemas.StreamsOut(
        activity_id=ride_id,
        start_s=start_s,
        end_s=end_s,
        step_s=step_s,
        sample_count=int(keys.size),
        time_s=[float(start_s + key * step_s) for key in keys],
        channels=out_channels,
    )

    size = len(json.dumps(result.model_dump(mode="json")))
    if size > STREAM_BUDGET_CHARS:
        ratio = size / STREAM_BUDGET_CHARS
        raise _invalid(
            f"The response would be {size} characters; the limit is {STREAM_BUDGET_CHARS}.",
            max_window_s_at_this_step=math.floor((end_s - start_s) / ratio),
            suggested_step_s=math.ceil(step_s * ratio * 1.1),
        )
    return result


# --- routes ------------------------------------------------------------------


def _route(route: Route, last: datetime | None) -> schemas.RouteOut:
    return schemas.RouteOut(
        id=route.id or 0,
        name=route.name,
        ride_count=route.ride_count,
        avg_distance_m=route.avg_distance_m,
        avg_elevation_gain_m=route.avg_elevation_gain_m,
        avg_moving_time_s=route.avg_moving_time_s,
        last_ridden_local_date=to_local(last).date() if last else None,
    )


def list_routes(session: Session) -> schemas.RoutesOut:
    """The Routes page, in its order: most ridden first, then most recent."""
    latest = last_ridden_by_route(session)
    pairs = [(route, latest.get(route.id or 0)) for route in session.exec(select(Route)).all()]
    pairs.sort(key=lambda pair: (pair[0].ride_count, pair[1] or datetime.min), reverse=True)
    routes = [_route(route, last) for route, last in pairs]
    return schemas.RoutesOut(count=len(routes), routes=routes)


def route_history(
    session: Session, route_id: int, *, include_excluded: bool
) -> schemas.RouteHistoryOut:
    """The route detail page: its summary card and its rides table."""
    route = session.get(Route, route_id)
    if route is None:
        raise _not_found(f"Route {route_id}")

    rides = list(
        session.exec(
            select(Ride).where(Ride.route_id == route_id).order_by(col(Ride.start_time).desc())
        ).all()
    )
    ids = [ride.id for ride in rides if ride.id is not None]
    excluded = excluded_ride_ids(session, ids)
    tags = load_ride_tags(session, ids)
    notes = load_note_summaries(session, ids)
    bikes = _bike_names(session)
    counted = [ride for ride in rides if ride.id not in excluded]
    summary = route_summary(counted)

    passes = [
        schemas.RoutePassOut(
            activity_id=ride.id or 0,
            name=ride.name,
            **_when(ride.start_time),
            bike_id=ride.bike_id,
            bike_name=bikes.get(ride.bike_id) if ride.bike_id is not None else None,
            moving_time_s=ride.moving_time_s,
            avg_speed_ms=ride.avg_speed_ms,
            avg_power_w=ride.avg_power_w,
            normalized_power_w=ride.normalized_power_w,
            best_power_1200s_w=ride.best_power_1200s_w,
            intensity_factor=ride.intensity_factor,
            tss=ride.tss,
            avg_hr_bpm=ride.avg_hr_bpm,
            tags=[pill.name for pill in tags.get(ride.id or 0, [])],
            excluded=ride.id in excluded,
            note_summary=notes.get(ride.id or 0),
        )
        for ride in rides
        if include_excluded or ride.id not in excluded
    ]
    last = max((ride.start_time for ride in counted), default=None)
    return schemas.RouteHistoryOut(
        route=_route(route, last),
        include_excluded=include_excluded,
        rides_in_analysis=len(counted),
        rides_excluded=len(rides) - len(counted),
        fastest_moving_time_s=summary.fastest_moving_time_s,
        fastest_on_local_date=to_local(summary.fastest_on).date() if summary.fastest_on else None,
        avg_tss=summary.avg_tss,
        best_tss=summary.best_tss,
        passes=passes,
    )


# --- fitness -----------------------------------------------------------------


def fitness(session: Session, now: datetime) -> schemas.FitnessOut:
    """The Fitness page: evaluation history and the best-effort windows."""
    evaluations = session.exec(select(FitnessEval).order_by(col(FitnessEval.valid_from))).all()
    rides = session.exec(analysis_rides().order_by(col(Ride.start_time))).all()

    curves = []
    for days in CURVE_WINDOW_DAYS:
        for effort in power_curve_windows(rides, window_days=days, now=now):
            curves.append(
                schemas.CurveWindowOut(
                    window_days=days,
                    label=effort.label,
                    start_local_date=to_local(effort.start).date(),
                    end_local_date=to_local(effort.end).date(),
                    ride_count=effort.ride_count,
                    **{
                        f"best_power_{duration}s_w": effort.best.get(duration)
                        for duration in metrics.POWER_CURVE_WINDOWS_S
                    },
                )
            )
    return schemas.FitnessOut(
        # Newest first, as the page's history table lists them.
        evaluations=[_evaluation(record) for record in reversed(evaluations)],
        power_curves=curves,
    )
