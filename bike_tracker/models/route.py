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

from pydantic import field_validator
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
