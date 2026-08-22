"""Bikes view state (masterplan §2.6).

Flat state: subclasses ``rx.State`` directly, shared via
``await self.get_state(BikesState)`` rather than inheritance.

**Why the sensor rows are controlled while name and weight are not.** The
house dialog pattern uses uncontrolled inputs with ``default_value`` plus a
``form_key`` bumped on open, so React remounts and picks the prefill up. That
works for fields the user only types into — but the sensor list is mutated
*while the dialog is open*: adding or deleting a row would have to bump the
key, remount the form, and throw away a half-typed name. So the sensor rows
live in state and are edited through handlers, while name, weight and notes
stay uncontrolled and arrive in one dict at submit.
"""

from dataclasses import dataclass, field
from typing import Any

import reflex as rx
from sqlmodel import col, select

from ..components.toast import notify_error, notify_success
from ..ingest.importer import recalculate_odometer
from ..models import SENSOR_TYPES, Bike, BikeForm, BikeSensor, Ride
from ..utils.formatting import format_distance_km, format_number
from ..utils.logging import get_logger
from ..utils.validation import parse_form

log = get_logger(__name__).bind(state="BikesState")

_SENSOR_LABELS = dict(SENSOR_TYPES)

# ANT+ device numbers are 16-bit; 0 means "unpaired" and is never a real
# sensor, so it is rejected rather than stored as a wildcard.
ANT_ID_MIN = 1
ANT_ID_MAX = 65535


@dataclass
class SensorBadge:
    """One sensor as shown on a bike card."""

    label: str = ""
    ant_id: str = ""


@dataclass
class BikeCard:
    """A bike as shown on the Bikes page — everything pre-formatted.

    Cards render inside ``rx.foreach``, where no per-item Python runs, so
    every string has to exist before the frontend sees it.
    """

    id: int = 0
    name: str = ""
    weight_kg: str = ""
    odo_km: str = ""
    notes: str = ""
    ride_count: int = 0
    ride_summary: str = ""
    sensors: list[SensorBadge] = field(default_factory=list)


@dataclass
class SensorRow:
    """One editable sensor row inside the dialog.

    ``ant_id`` is a string because it is bound to a text input; it is
    validated and converted on submit. ``error`` carries the per-row message.
    """

    key: int = 0
    sensor_type: str = "power_meter"
    ant_id: str = ""
    label: str = ""
    error: str = ""


class BikesState(rx.State):
    bikes: list[BikeCard] = []

    dialog_open: bool = False
    confirm_open: bool = False
    recalculating: bool = False

    editing_id: int | None = None
    pending_delete_id: int | None = None
    pending_delete_name: str = ""
    pending_delete_rides: int = 0

    form_errors: dict[str, str] = {}
    form_key: int = 0
    form_name: str = ""
    form_weight: str = "0"
    form_notes: str = ""

    sensor_rows: list[SensorRow] = []
    _next_sensor_key: int = 0

    @rx.var
    def dialog_title(self) -> str:
        return "Edit bike" if self.editing_id is not None else "New bike profile"

    @rx.var
    def submit_label(self) -> str:
        return "Save changes" if self.editing_id is not None else "Create"

    @rx.var
    def is_editing(self) -> bool:
        """The odometer can only be recalculated for a bike that exists."""
        return self.editing_id is not None

    @rx.var
    def delete_description(self) -> str:
        """Say exactly what the delete will cost before it happens."""
        if self.pending_delete_rides == 0:
            return "This bike has no rides. Its sensors will be removed with it."
        return (
            f"{self.pending_delete_rides} ride(s) will become Unassigned, and this "
            "bike's sensors will be removed. The rides themselves are kept."
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Page ``on_load`` — rebuild the card list."""
        self._reload()

    def _reload(self) -> None:
        with rx.session() as session:
            bikes = session.exec(select(Bike).order_by(col(Bike.name))).all()
            sensors = session.exec(select(BikeSensor).order_by(col(BikeSensor.id))).all()
            rides = session.exec(select(Ride)).all()

        by_bike: dict[int, list[BikeSensor]] = {}
        for sensor in sensors:
            by_bike.setdefault(sensor.bike_id, []).append(sensor)

        counts: dict[int | None, int] = {}
        for ride in rides:
            counts[ride.bike_id] = counts.get(ride.bike_id, 0) + 1

        self.bikes = [
            _to_card(bike, by_bike.get(bike.id or 0, []), counts.get(bike.id, 0)) for bike in bikes
        ]
        log.info("bikes.load", count=len(self.bikes))

    # ------------------------------------------------------------------
    # Dialog
    # ------------------------------------------------------------------

    @rx.event
    def open_create(self):
        self.editing_id = None
        self.form_errors = {}
        self.form_name = ""
        self.form_weight = "0"
        self.form_notes = ""
        self.sensor_rows = []
        self._next_sensor_key = 0
        self.form_key += 1
        self.dialog_open = True

    @rx.event
    def open_edit(self, bike_id: int):
        with rx.session() as session:
            bike = session.get(Bike, bike_id)
            if bike is None:
                log.warning("bikes.edit.missing", bike_id=bike_id)
                return notify_error("That bike no longer exists.")
            sensors = session.exec(
                select(BikeSensor).where(BikeSensor.bike_id == bike_id).order_by(col(BikeSensor.id))
            ).all()

        self.editing_id = bike_id
        self.form_errors = {}
        self.form_name = bike.name
        self.form_weight = format_number(bike.weight_kg, 1)
        self.form_notes = bike.notes or ""
        self.sensor_rows = [
            SensorRow(
                key=index,
                sensor_type=sensor.sensor_type,
                ant_id=str(sensor.ant_id),
                label=sensor.label or "",
            )
            for index, sensor in enumerate(sensors)
        ]
        self._next_sensor_key = len(self.sensor_rows)
        self.form_key += 1
        self.dialog_open = True
        return None

    @rx.event
    def set_dialog_open(self, value: bool):
        """Bound to ``on_open_change`` so Escape and click-outside also clear."""
        self.dialog_open = value
        if not value:
            self.form_errors = {}

    # ------------------------------------------------------------------
    # Sensor rows
    # ------------------------------------------------------------------

    @rx.event
    def add_sensor_row(self):
        self.sensor_rows = [
            *self.sensor_rows,
            SensorRow(key=self._next_sensor_key, sensor_type=SENSOR_TYPES[0][0]),
        ]
        self._next_sensor_key += 1

    @rx.event
    def remove_sensor_row(self, key: int):
        self.sensor_rows = [row for row in self.sensor_rows if row.key != key]

    @rx.event
    def set_sensor_type(self, key: int, value: str):
        self._update_row(key, sensor_type=value)

    @rx.event
    def set_sensor_ant_id(self, key: int, value: str):
        self._update_row(key, ant_id=value, error="")

    @rx.event
    def set_sensor_label(self, key: int, value: str):
        self._update_row(key, label=value)

    def _update_row(self, key: int, **changes: str) -> None:
        """Replace the row in place.

        Rebuilding the list rather than mutating a dataclass in place is what
        makes Reflex notice the change — a mutation inside an element of a
        list var does not mark the var dirty.
        """
        self.sensor_rows = [
            SensorRow(**{**row.__dict__, **changes}) if row.key == key else row
            for row in self.sensor_rows
        ]

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    @rx.event
    def submit_form(self, form_data: dict[str, Any]):
        """Cast, validate, persist — bike and sensors in one transaction.

        On failure the handler returns early, which leaves ``dialog_open``
        True so the user keeps their input and sees per-field messages.
        """
        parsed, errors = parse_form(form_data, BikeForm)
        rows, row_errors = self._validated_sensor_rows()

        if errors or parsed is None or row_errors:
            self.form_errors = errors
            self.sensor_rows = rows
            # Keep what was typed so the remount does not wipe it.
            self.form_name = str(form_data.get("name", ""))
            self.form_weight = str(form_data.get("weight_kg", ""))
            self.form_notes = str(form_data.get("notes", ""))
            log.info("bikes.submit.invalid", fields=sorted(errors), rows=row_errors)
            return None

        self.form_errors = {}
        with rx.session() as session:
            if self.editing_id is None:
                bike = Bike(**parsed.model_dump())
                session.add(bike)
                session.commit()
                session.refresh(bike)
                message = f"Created “{bike.name}”."
            else:
                existing = session.get(Bike, self.editing_id)
                if existing is None:
                    self.dialog_open = False
                    log.warning("bikes.update.missing", bike_id=self.editing_id)
                    return notify_error("That bike no longer exists.")
                for name, value in parsed.model_dump().items():
                    setattr(existing, name, value)
                session.add(existing)
                session.commit()
                bike = existing
                message = f"Updated “{bike.name}”."

            # Read what the log and toast need *before* the sensor rewrite
            # commits: a commit expires every instance in the session, so
            # touching `bike` after this line raises DetachedInstanceError
            # once the session closes.
            bike_id = bike.id or 0
            _replace_sensors(session, bike_id, rows)

        log.info("bikes.save", bike_id=bike_id, sensors=len(rows))
        self.dialog_open = False
        self.editing_id = None
        self._reload()
        return notify_success(message)

    def _validated_sensor_rows(self) -> tuple[list[SensorRow], list[str]]:
        """Check every ANT+ id, annotating the rows that fail.

        A duplicate id *within one bike* is a mistake and is rejected. A
        duplicate *across* bikes is not: the same chest strap is deliberately
        registered on both bikes, and ``resolve_bike`` already discards ids
        that more than one bike claims.
        """
        checked: list[SensorRow] = []
        problems: list[str] = []
        seen: dict[int, int] = {}

        for row in self.sensor_rows:
            error = ""
            raw = row.ant_id.strip()
            if not raw:
                error = "Enter the ANT+ id."
            else:
                try:
                    ant_id = int(raw)
                except ValueError:
                    error = "Whole number only."
                else:
                    if not ANT_ID_MIN <= ant_id <= ANT_ID_MAX:
                        error = f"Between {ANT_ID_MIN} and {ANT_ID_MAX}."
                    elif ant_id in seen:
                        error = "Already on this bike."
                    else:
                        seen[ant_id] = row.key

            if error:
                problems.append(f"{row.sensor_type}: {error}")
            checked.append(SensorRow(**{**row.__dict__, "error": error}))

        return checked, problems

    # ------------------------------------------------------------------
    # Odometer
    # ------------------------------------------------------------------

    @rx.event
    def recalculate_odometer(self):
        """Rebuild the odometer from the rides currently on this bike."""
        if self.editing_id is None:
            return

        self.recalculating = True
        yield

        with rx.session() as session:
            total = recalculate_odometer(session, self.editing_id)

        self.recalculating = False
        self._reload()
        yield notify_success(f"Odometer recalculated to {format_distance_km(total)} km.")

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    @rx.event
    def ask_delete(self, bike_id: int):
        with rx.session() as session:
            bike = session.get(Bike, bike_id)
            if bike is None:
                return notify_error("That bike no longer exists.")
            rides = session.exec(select(Ride).where(Ride.bike_id == bike_id)).all()

        self.pending_delete_id = bike_id
        self.pending_delete_name = bike.name
        self.pending_delete_rides = len(rides)
        self.confirm_open = True
        return None

    @rx.event
    def set_confirm_open(self, value: bool):
        self.confirm_open = value
        if not value:
            self.pending_delete_id = None

    @rx.event
    def confirm_delete(self):
        """Unassign the rides, drop the sensors, then delete the bike.

        All three are required because the database provides no cascade —
        SQLite is not even enforcing the foreign keys here. Dropping the
        sensors matters most: ``resolve_bike`` looks them up by ``ant_id``
        alone and never joins ``bike``, so an orphaned row would keep voting
        for a bike that no longer exists and mis-assign future imports.
        """
        bike_id = self.pending_delete_id
        self.confirm_open = False
        self.pending_delete_id = None
        if bike_id is None:
            return None

        with rx.session() as session:
            bike = session.get(Bike, bike_id)
            if bike is None:
                return notify_error("That bike no longer exists.")
            name = bike.name

            rides = session.exec(select(Ride).where(Ride.bike_id == bike_id)).all()
            for ride in rides:
                ride.bike_id = None
                session.add(ride)

            sensors = session.exec(select(BikeSensor).where(BikeSensor.bike_id == bike_id)).all()
            for sensor in sensors:
                session.delete(sensor)

            session.delete(bike)
            session.commit()

        log.info("bikes.delete", bike_id=bike_id, unassigned=len(rides), sensors=len(sensors))
        self._reload()
        return notify_success(
            f"Deleted “{name}”."
            if not rides
            else f"Deleted “{name}”; {len(rides)} ride(s) are now unassigned."
        )


# ---------------------------------------------------------------------------
# View-model mapping
# ---------------------------------------------------------------------------


def _to_card(bike: Bike, sensors: list[BikeSensor], ride_count: int) -> BikeCard:
    return BikeCard(
        id=bike.id or 0,
        name=bike.name,
        weight_kg=format_number(bike.weight_kg, 1),
        odo_km=format_distance_km(bike.odo_m),
        notes=bike.notes or "",
        ride_count=ride_count,
        ride_summary=f"{ride_count} ride" if ride_count == 1 else f"{ride_count} rides",
        sensors=[
            SensorBadge(
                label=_SENSOR_LABELS.get(sensor.sensor_type, sensor.sensor_type),
                ant_id=str(sensor.ant_id),
            )
            for sensor in sensors
        ],
    )


def _replace_sensors(session: Any, bike_id: int, rows: list[SensorRow]) -> None:
    """Make the stored sensors match the dialog's rows.

    Replaced wholesale rather than diffed: a bike has a handful of sensors,
    and rewriting them is both simpler and immune to the row-identity bugs a
    diff would invite. ``BikeSensor.id`` is not referenced anywhere else, so
    nothing depends on the rows keeping their primary keys.
    """
    existing = session.exec(select(BikeSensor).where(BikeSensor.bike_id == bike_id)).all()
    for sensor in existing:
        session.delete(sensor)

    for row in rows:
        session.add(
            BikeSensor(
                bike_id=bike_id,
                sensor_type=row.sensor_type,
                ant_id=int(row.ant_id),
                label=row.label.strip() or None,
            )
        )
    session.commit()
