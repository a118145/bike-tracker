"""Rides, their laps, and their per-sample streams.

Field names carry unit suffixes (brief §6) so the REST and MCP layers stay
pure pass-throughs — a rename at that boundary is where translation bugs move
in.

Every measured quantity lives on :class:`~.metric_fields.MetricFields`, which
``Ride`` and ``Lap`` both inherit, so a lap shows the same thirty-odd stat
cards a ride does. This module holds only what distinguishes them: a ride's
identity and relations, a lap's position within its ride.
"""

from datetime import UTC, datetime

from reflex.model import ModelRegistry
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .metric_fields import LOAD_SOURCE_HR, LOAD_SOURCE_POWER, MetricFields

__all__ = [
    "LOAD_SOURCE_HR",
    "LOAD_SOURCE_POWER",
    "Lap",
    "MetricFields",
    "Ride",
    "RideNote",
    "RideStream",
]


@ModelRegistry.register
class Ride(MetricFields, table=True):
    """One recorded activity."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="Ride", index=True)

    # --- identity / provenance --------------------------------------------
    # Rides are deduplicated on the FIT payload hash: FIT files themselves are
    # never persisted (masterplan §1.3), so this is the only thing left that
    # can say "already imported".
    fit_hash: str = Field(index=True, unique=True)
    source: str = Field(default="file")
    external_id: str | None = Field(default=None, index=True)
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- relations ---------------------------------------------------------
    bike_id: int | None = Field(default=None, foreign_key="bike.id", index=True)
    route_id: int | None = Field(default=None, foreign_key="route.id", index=True)
    # Croozer trailer, trekking bike only (brief §2). Default is "no trailer";
    # without the flag, trailer rides poison every comparison they appear in.
    has_trailer: bool = Field(default=False)

    # --- identity in time --------------------------------------------------
    start_time: datetime = Field(index=True)
    end_time: datetime
    sport: str = Field(default="cycling")
    num_laps: int = Field(default=0)

    # Seconds between ``start_time`` (the FIT session start) and the first
    # record, which is where ``RideStream.time_s`` has its origin. They differ
    # by two seconds on the evening reference ride, and without this column
    # every lap window would be offset by that much when sliced from the
    # stored streams.
    stream_offset_s: float = Field(default=0.0)

    # --- what the head unit itself was configured with ---------------------
    # Kept because the FIT file is not (masterplan §1.3), and it is the second
    # step of the FTP fallback chain: newest evaluation at or before the ride,
    # else this, else nothing. Without it, deleting the last evaluation has
    # nothing to fall back to and the ride keeps a number no longer backed by
    # anything. Provenance rather than a metric, so it lives on Ride and not
    # on MetricFields — a lap has no head-unit reading of its own, it takes
    # its parent ride's resolved value.
    device_ftp_w: int | None = Field(default=None)
    # The Karoo writes no threshold heart rate (only `max_heart_rate`, which
    # is a different quantity and must not be borrowed for this). Populated
    # only if a head unit ever does write `threshold_heart_rate`.
    device_lthr_bpm: int | None = Field(default=None)

    # SVG "points" attribute for the list-card thumbnail, projected once at
    # import (brief §1: precompute rather than recompute per query). Without
    # it, rendering a 40-card Rides list means decompressing 40 GPS streams on
    # every page load. Empty string means the ride has no usable track.
    preview_points: str = Field(default="")

    # The ride's own track as [[lat, lon], ...], Douglas-Peucker simplified at
    # the same 15 m the route geometry uses. Distinct from `preview_points`,
    # which is SVG viewbox coordinates with latitude scaled by cos(mean_lat)
    # and y negated — unrecoverable back to real coordinates.
    #
    # Precomputed because the route heat map draws one line per matched ride.
    # Rebuilding these from `RideStream` costs ~20 ms per ride (the decode is
    # cheap; Douglas-Peucker is not), so a 100-ride commuting route would
    # spend seconds re-deriving identical geometry on every page load.
    # `import_ride` already has the decoded track in hand, so writing it here
    # is one extra simplify call at import and none afterwards.
    map_points: list[list[float]] = Field(default_factory=list, sa_column=Column(JSON))


@ModelRegistry.register
class Lap(MetricFields, table=True):
    """One lap of a ride, in recording order.

    Carries the full metric set, computed over that lap's slice of the ride's
    streams — see ``ingest/summarize.py``. ``index`` is zero-based; the UI
    labels laps from 1 (masterplan §2.4.2, ``docs/image_2.png``).
    """

    id: int | None = Field(default=None, primary_key=True)
    ride_id: int = Field(foreign_key="ride.id", index=True)
    index: int = Field(default=0)

    start_time: datetime
    end_time: datetime


@ModelRegistry.register
class RideStream(SQLModel, table=True):
    """Per-sample channels for one ride — **one row per ride, not per sample**.

    A 3 h ride at 1 Hz is ~10,000 samples per channel across ten channels. As
    rows that is 100,000 inserts for a single ride and a table that grows into
    the tens of millions within a season; as ten compressed blobs it is a
    handful of kilobytes fetched in a single primary-key read.

    Each channel is a zlib-compressed little-endian ``float32`` buffer of
    ``sample_count`` values, written and read by ``ingest.streams``. NaN marks
    a missing sample, which is why even the integer-valued channels (heart
    rate, cadence) are stored as float.
    """

    id: int | None = Field(default=None, primary_key=True)
    ride_id: int = Field(foreign_key="ride.id", index=True, unique=True)
    sample_count: int = Field(default=0)

    time_s: bytes | None = Field(default=None)
    lat: bytes | None = Field(default=None)
    lon: bytes | None = Field(default=None)
    altitude_m: bytes | None = Field(default=None)
    distance_m: bytes | None = Field(default=None)
    speed_ms: bytes | None = Field(default=None)
    power_w: bytes | None = Field(default=None)
    hr_bpm: bytes | None = Field(default=None)
    cadence_rpm: bytes | None = Field(default=None)
    temperature_c: bytes | None = Field(default=None)
    grade_pct: bytes | None = Field(default=None)
    # Left-leg share of total power, per sample. Only the dual-sided Assioma
    # reports it, but storing it per sample is what lets a *lap* have its own
    # balance rather than borrowing the ride's.
    left_balance_pct: bytes | None = Field(default=None)


@ModelRegistry.register
class RideNote(SQLModel, table=True):
    """Training-diary entry for a ride.

    No UI in milestone 1. It is modelled now because brief §5.5 lists the
    structured fields as a mandatory data-model requirement — free text alone
    cannot be correlated — and adding the table later is another migration.

    ``perceived_breathing_limit`` is the only data source on the ventilation
    threshold short of spirometry; over ~30 rides it becomes a pattern.
    """

    id: int | None = Field(default=None, primary_key=True)
    ride_id: int = Field(foreign_key="ride.id", index=True, unique=True)

    sleep_quality: int | None = Field(default=None)  # 1..5
    rpe: int | None = Field(default=None)  # Borg CR10, 1..10
    temperature_c: float | None = Field(default=None)
    perceived_breathing_limit: int | None = Field(default=None)  # 1..5
    text: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
