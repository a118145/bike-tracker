"""Derived routes.

A route is not planned by hand — it is the shape that emerges when several
rides follow the same path. Import matches each new ride against every
existing route and either joins one or creates a new one.

Deviation from ``masterplan.md`` §3.2: ``rides: list[int]`` becomes the
reverse foreign key ``Ride.route_id``, for the same reason as on ``Bike``.
The averages that column was meant to feed are stored here instead, updated
whenever a ride joins or leaves.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import field_validator
from reflex.model import ModelRegistry
from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, col, func, select

from .ride import Ride, in_analysis


@ModelRegistry.register
class Route(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)

    # Averages over the rides currently assigned to this route that count in
    # analysis (`in_analysis()`). Recomputed on assignment or exclusion
    # change rather than at query time (brief §1: precompute). `None` when no
    # ride on the route counts: the average does not exist, and 0.0 would
    # reach the MCP layer as a real value.
    avg_distance_m: float | None = Field(default=None)
    avg_elevation_gain_m: float | None = Field(default=None)
    avg_moving_time_s: float | None = Field(default=None)
    ride_count: int = Field(default=0)

    # Simplified representative polyline as [[lat, lon], ...]. Simplified, not
    # raw: this is what route matching compares against and what the list-card
    # previews draw, and a raw 1 Hz track is ~10k points per ride.
    #
    # Written once, from the first ride that created the route, and never
    # revisited — so a route can outlive the ride whose geometry defines it.
    # Re-deriving a representative track from the current members would be a
    # separate feature; it is deliberately not one today.
    points: list[list[float]] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RouteForm(SQLModel):
    """Non-table form model for the rename dialog.

    Only the name: everything else on a route is derived from its rides and
    must not be typed in.
    """

    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Reject whitespace-only names and store the trimmed one.

        ``min_length=1`` alone accepts "   ", which then shows as a blank
        route in the list and in every ride's route dropdown.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name must not be blank.")
        return stripped


@dataclass(frozen=True)
class RouteSummary:
    """What the route card says beyond the stored averages. ``None`` = no data."""

    fastest_moving_time_s: float | None = None
    fastest_on: datetime | None = None
    avg_tss: float | None = None
    best_tss: float | None = None


def route_summary(rides: Sequence[Ride]) -> RouteSummary:
    """Fastest pass and TSS figures over ``rides`` — pass only rides in analysis.

    Shared by the route detail card and the REST API, so the two report the
    same fastest pass. Ties go to the earliest ride: the record was set then.
    """
    times = [r.moving_time_s for r in rides if r.moving_time_s > 0]
    loads = [r.tss for r in rides if r.tss is not None]

    fastest = min(times) if times else None
    fastest_ride = (
        min((r for r in rides if r.moving_time_s == fastest), key=lambda r: r.start_time)
        if fastest is not None
        else None
    )
    return RouteSummary(
        fastest_moving_time_s=fastest,
        fastest_on=fastest_ride.start_time if fastest_ride else None,
        avg_tss=sum(loads) / len(loads) if loads else None,
        best_tss=max(loads) if loads else None,
    )


def last_ridden_by_route(session: Session) -> dict[int, datetime]:
    """When each route was last ridden, counting rides in analysis only.

    One grouped query for a whole list. SQLite hands an aggregated timestamp
    back as a string rather than a ``datetime``, so it is parsed here, once,
    for the Routes page and the REST API alike.
    """
    rows = session.exec(
        select(col(Ride.route_id), func.max(col(Ride.start_time)))
        .where(in_analysis())
        .group_by(col(Ride.route_id))
    ).all()
    found: dict[int, datetime] = {}
    for route_id, value in rows:
        parsed = _as_datetime(value)
        if route_id is not None and parsed is not None:
            found[route_id] = parsed
    return found


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
