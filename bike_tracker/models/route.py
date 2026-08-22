"""Derived routes.

A route is not planned by hand — it is the shape that emerges when several
rides follow the same path. Import matches each new ride against every
existing route and either joins one or creates a new one.

Deviation from ``masterplan.md`` §3.2: ``rides: list[int]`` becomes the
reverse foreign key ``Ride.route_id``, for the same reason as on ``Bike``.
The averages that column was meant to feed are stored here instead, updated
whenever a ride joins or leaves.
"""

from datetime import UTC, datetime

from reflex.model import ModelRegistry
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


@ModelRegistry.register
class Route(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)

    # Averages over the rides currently assigned to this route. Recomputed on
    # assignment change rather than at query time (brief §1: precompute).
    avg_distance_m: float = Field(default=0.0)
    avg_elevation_gain_m: float = Field(default=0.0)
    avg_moving_time_s: float = Field(default=0.0)
    ride_count: int = Field(default=0)

    # Simplified representative polyline as [[lat, lon], ...]. Simplified, not
    # raw: this is what route matching compares against and what the list-card
    # previews draw, and a raw 1 Hz track is ~10k points per ride.
    points: list[list[float]] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
