"""Bike profiles and their sensors.

Deviation from ``masterplan.md`` §3.1, which specifies ``sensors:
dict[str, int]`` and ``rides: list[int]`` as columns on ``Bike``:

* ``sensors`` becomes the :class:`BikeSensor` table. Auto-detection queries
  *by* ``ant_id`` — "which bike owns ANT+ device 13522?" — and a JSON column
  cannot be indexed for that lookup.
* ``rides`` becomes the reverse foreign key ``Ride.bike_id``. A list of
  foreign keys in a column has no referential integrity and cannot be joined.
"""

from datetime import UTC, datetime

from pydantic import field_validator
from reflex.model import ModelRegistry
from sqlmodel import Field, SQLModel

# Sensor kinds offered by the bike dialog. The value is what lands in
# ``BikeSensor.sensor_type``; the label is what the form shows.
SENSOR_TYPES: list[tuple[str, str]] = [
    ("power_meter", "Power meter"),
    ("heart_rate", "Heart rate monitor"),
    ("speed", "Speed sensor"),
    ("cadence", "Cadence sensor"),
    ("speed_cadence", "Speed + cadence sensor"),
    ("shifting", "Electronic shifting"),
    ("radar", "Radar"),
    ("other", "Other"),
]


@ModelRegistry.register
class Bike(SQLModel, table=True):
    """A bike profile.

    ``weight_kg`` is the *system* weight (rider plus bike) per masterplan
    §2.6, which is what climbing and power-to-weight numbers need.
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    weight_kg: float = Field(default=0.0)

    # Maintained incrementally as rides are assigned and unassigned (brief §2);
    # a full recalculation from the current rides can be triggered from the
    # bike's edit dialog.
    odo_m: float = Field(default=0.0)

    notes: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@ModelRegistry.register
class BikeSensor(SQLModel, table=True):
    """One ANT+ device belonging to a bike.

    ``ant_id`` is the FIT ``device_info.ant_device_number``. It is indexed
    because ride import looks bikes up by it.

    Note that a sensor id is *not* unique across bikes: the same Decathlon
    chest strap is used on both bikes (brief §2), so 44050 legitimately
    appears twice. ``resolve_bike`` handles that by ignoring sensor ids that
    more than one bike claims.
    """

    id: int | None = Field(default=None, primary_key=True)
    bike_id: int = Field(foreign_key="bike.id", index=True)
    sensor_type: str = Field(default="other")
    ant_id: int = Field(index=True)
    label: str | None = Field(default=None)


class BikeForm(SQLModel):
    """Non-table form model for the bike dialog.

    Separate from :class:`Bike` because the form must not accept ``odo_m``
    (server-owned, masterplan §3.1) or ``created_at``.
    """

    name: str = Field(min_length=1, max_length=100)
    weight_kg: float = Field(ge=0, le=500)
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Reject whitespace-only names and store the trimmed one.

        ``min_length=1`` alone accepts "   ", which then shows as a blank
        bike in the sidebar dropdown and cannot be told apart from any other
        blank one.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name must not be blank.")
        return stripped
