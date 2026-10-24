"""Rides, their laps, and their per-sample streams.

Field names carry unit suffixes (brief §6) so the REST and MCP layers stay
pure pass-throughs — a rename at that boundary is where translation bugs move
in.

Every measured quantity lives on :class:`~.metric_fields.MetricFields`, which
``Ride`` and ``Lap`` both inherit, so a lap shows the same thirty-odd stat
cards a ride does. This module holds only what distinguishes them: a ride's
identity and relations, a lap's position within its ride.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import field_validator
from reflex.model import ModelRegistry
from sqlalchemy import JSON, Column, ColumnElement, not_
from sqlmodel import Field, Session, SQLModel, col, select
from sqlmodel.sql.expression import SelectOfScalar

from .metric_fields import LOAD_SOURCE_HR, LOAD_SOURCE_POWER, MetricFields
from .tag import TAG_EFFECT_EXCLUDE, RideTag, Tag

__all__ = [
    "LOAD_SOURCE_HR",
    "LOAD_SOURCE_POWER",
    "Lap",
    "MetricFields",
    "Ride",
    "RideNote",
    "RideNoteForm",
    "RideStream",
    "analysis_rides",
    "excluded_ride_ids",
    "in_analysis",
    "lap_window_s",
    "load_note_summaries",
    "note_summary",
    "save_ride_note",
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


def in_analysis() -> ColumnElement[bool]:
    """The predicate behind :func:`analysis_rides`, for queries that select
    columns or aggregates rather than whole rides.

    A ride is out of analysis when it carries a tag whose *effect* excludes it
    — a ride that really happened but describes no training, such as a test
    with implausible power. The tag's name plays no part (models/tag.py).
    """
    excluding = (
        select(RideTag.ride_id)
        .join(Tag, col(Tag.id) == col(RideTag.tag_id))
        .where(Tag.effect == TAG_EFFECT_EXCLUDE)
    )
    return col(Ride.id).not_in(excluding)


def analysis_rides() -> SelectOfScalar[Ride]:
    """Every ride that counts as training data — the one definition of it.

    Fitness, dashboard and route statistics query through this, and so will
    the MCP layer, so they all describe the same set of rides. The odometer,
    deduplication, the FTP rewrite and the route backfill deliberately do not:
    an excluded ride still wore the bike and still exists. See AGENTS.md
    rule 42. Callers add their own ordering.
    """
    return select(Ride).where(in_analysis())


def excluded_ride_ids(session: Session, ride_ids: Iterable[int]) -> set[int]:
    """Which of ``ride_ids`` are out of analysis, by the same predicate.

    For code that already holds the rides and needs a flag per ride — the
    route table dims them, route statistics skip them — without asking the
    tags a second, hand-written way.
    """
    ids = list(ride_ids)
    if not ids:
        return set()
    rows = session.exec(select(Ride.id).where(col(Ride.id).in_(ids), not_(in_analysis()))).all()
    return {row for row in rows if row is not None}


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


def lap_window_s(ride: Ride, lap: Lap) -> tuple[float, float]:
    """A lap's ``(start_s, end_s)`` on the stream clock (``RideStream.time_s``).

    The stream's clock starts at the first record, the ride's at the session
    start — two seconds apart on the evening reference ride — so both ends are
    shifted by ``stream_offset_s``. This is the window import summarized the
    lap over; the ride page's lap selection and the API's window cutter use it
    too, so all three cut the same samples.
    """
    start_s = (lap.start_time - ride.start_time).total_seconds() - ride.stream_offset_s
    end_s = (lap.end_time - ride.start_time).total_seconds() - ride.stream_offset_s
    return start_s, end_s


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
    """Training-diary entry for a ride, edited from the ride page's note button.

    The structured fields sit beside the free text because brief §5.5 makes
    them a data-model requirement: free text alone cannot be correlated.
    ``perceived_breathing_limit`` is the only data source on the ventilation
    threshold short of spirometry; over ~30 rides it becomes a pattern.

    **A note exists exactly when its row does** (AGENTS.md rule 44). An
    emptied note is deleted, never stored blank, so "has a note" is one
    question with one answer for the ride page, the lists and the MCP. Write
    only through :func:`save_ride_note`.

    ``temperature_c`` has no form field: the Karoo's own channel is the
    reference. The column stays for a reading taken by hand.
    """

    id: int | None = Field(default=None, primary_key=True)
    ride_id: int = Field(foreign_key="ride.id", index=True, unique=True)

    sleep_quality: int | None = Field(default=None)  # 1..5
    rpe: int | None = Field(default=None)  # Borg CR10, 1..10
    temperature_c: float | None = Field(default=None)
    perceived_breathing_limit: int | None = Field(default=None)  # 1..5
    text: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


NOTE_TEXT_MAX_LENGTH = 4000

# Upper ends of the structured scales. All three start at 1; "not recorded" is
# `None`, never 0 (rule 13).
SLEEP_QUALITY_MAX = 5
RPE_MAX = 10  # Borg CR10
BREATHING_LIMIT_MAX = 5

# How much of a long note a tooltip carries. Enough for a paragraph; the
# dialog holds the rest.
NOTE_TOOLTIP_MAX_CHARS = 300


class RideNoteForm(SQLModel):
    """Non-table form model for the note dialog (rule 6).

    Text is stripped and blank becomes ``None`` here, once, so whitespace can
    never make a note that looks empty count as one.
    """

    text: str | None = Field(default=None, max_length=NOTE_TEXT_MAX_LENGTH)
    sleep_quality: int | None = Field(default=None, ge=1, le=SLEEP_QUALITY_MAX)
    rpe: int | None = Field(default=None, ge=1, le=RPE_MAX)
    perceived_breathing_limit: int | None = Field(default=None, ge=1, le=BREATHING_LIMIT_MAX)

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @property
    def is_empty(self) -> bool:
        return (
            self.text is None
            and self.sleep_quality is None
            and self.rpe is None
            and self.perceived_breathing_limit is None
        )


def save_ride_note(session: Session, ride_id: int, form: RideNoteForm) -> RideNote | None:
    """Write a ride's note: upsert it, or delete it when the form is empty.

    The one write path for ``RideNote`` (rule 44). Returns the saved row, or
    ``None`` when the ride now has no note. Commits. ``temperature_c`` is not
    on the form and is left as it is.
    """
    note = session.exec(select(RideNote).where(RideNote.ride_id == ride_id)).first()
    if form.is_empty:
        if note is not None:
            session.delete(note)
            session.commit()
        return None

    if note is None:
        note = RideNote(ride_id=ride_id)
    note.text = form.text
    note.sleep_quality = form.sleep_quality
    note.rpe = form.rpe
    note.perceived_breathing_limit = form.perceived_breathing_limit
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def note_summary(note: RideNote, *, full_text: bool = False) -> str:
    """The note as a tooltip reads it: text, then "Sleep 2/5 · RPE 7 · Breathing 4/5".

    A list card or table cell gets the first line of the text; the ride page,
    which has room, gets the whole text up to ``NOTE_TOOLTIP_MAX_CHARS``. The
    two parts are on separate lines, and either may be missing.
    """
    lines: list[str] = []
    text = (note.text or "").strip()
    if text:
        if not full_text:
            text = text.splitlines()[0].strip()
        elif len(text) > NOTE_TOOLTIP_MAX_CHARS:
            text = text[:NOTE_TOOLTIP_MAX_CHARS].rstrip() + "…"
        lines.append(text)

    fields = [
        f"{label} {value}/{top}"
        for label, value, top in (
            ("Sleep", note.sleep_quality, SLEEP_QUALITY_MAX),
            ("RPE", note.rpe, RPE_MAX),
            ("Breathing", note.perceived_breathing_limit, BREATHING_LIMIT_MAX),
        )
        if value is not None
    ]
    if fields:
        lines.append(" · ".join(fields))
    return "\n".join(lines)


def load_note_summaries(session: Session, ride_ids: Iterable[int] | None = None) -> dict[int, str]:
    """First-line summaries per ride id, in one query — for a list, not per card.

    A ride without a note is absent from the result. ``ride_ids=None`` means
    all rides.
    """
    query = select(RideNote)
    if ride_ids is not None:
        query = query.where(col(RideNote.ride_id).in_(list(ride_ids)))
    return {note.ride_id: note_summary(note) for note in session.exec(query).all()}
