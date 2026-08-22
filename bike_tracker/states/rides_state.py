"""Rides list state (masterplan §2.4.1).

Flat state: subclasses ``rx.State`` directly, and other states reach it with
``await self.get_state(RidesState)`` rather than through inheritance.

The list is built as pre-formatted view models rather than raw ``Ride`` rows.
Cards render inside ``rx.foreach``, where no per-item Python runs, so every
string a card shows — the distance, the duration, the thumbnail geometry —
has to exist before the frontend sees it.
"""

from dataclasses import dataclass
from typing import Any

import reflex as rx
from sqlmodel import col, select

from ..components.toast import notify_error, notify_info, notify_success, notify_warning
from ..ingest.sources.file import import_bytes, scan_folder
from ..models import Bike, Ride
from ..navigation import routes
from ..utils.formatting import (
    format_distance_km,
    format_duration,
    format_number,
)
from ..utils.logging import get_logger

log = get_logger(__name__).bind(state="RidesState")

# Cap on a single upload. A FIT activity is a few hundred kilobytes; anything
# far larger is not a ride, and decoding it would block the event loop.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@dataclass
class RideCard:
    """One card in the rides list — everything pre-formatted for display."""

    id: int = 0
    name: str = ""
    date: str = ""
    distance_km: str = ""
    elevation_m: str = ""
    duration: str = ""
    bike_name: str = ""
    preview_points: str = ""
    detail_href: str = ""


class RidesState(rx.State):
    rides: list[RideCard] = []
    loading: bool = False
    uploading: bool = False

    @rx.var
    def has_rides(self) -> bool:
        return len(self.rides) > 0

    @rx.var
    def ride_count(self) -> int:
        return len(self.rides)

    @rx.event
    def load(self):
        """Page ``on_load`` — rebuild the card list, newest ride first."""
        self._reload()

    def _reload(self) -> None:
        """Rebuild the card list.

        A plain method rather than the event handler, so the import handlers
        can refresh the list without re-entering the Reflex event machinery.
        """
        with rx.session() as session:
            rides = session.exec(select(Ride).order_by(col(Ride.start_time).desc())).all()
            bike_names = {bike.id: bike.name for bike in session.exec(select(Bike)).all()}

        self.rides = [_to_card(ride, bike_names) for ride in rides]
        log.info("rides.load", count=len(self.rides))

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        """Import one or more FIT files chosen in the upload dialog.

        Each file is imported independently so one unreadable download costs
        that ride and not the rest of the batch.
        """
        if not files:
            return

        self.uploading = True
        yield

        created = 0
        duplicates = 0
        failures: list[str] = []
        warnings: list[str] = []

        for upload in files:
            name = upload.name or "upload.fit"
            try:
                data = await upload.read()
            except OSError as exc:  # pragma: no cover - transport failure
                failures.append(f"{name}: {exc}")
                continue

            if len(data) > MAX_UPLOAD_BYTES:
                failures.append(f"{name}: file is too large to be a FIT activity.")
                continue

            try:
                with rx.session() as session:
                    result = import_bytes(data, name, session)
            except Exception as exc:
                log.warning("rides.upload.failed", filename=name, error=str(exc))
                failures.append(f"{name}: {exc}")
                continue

            if result.created:
                created += 1
                warnings.extend(result.warnings)
            else:
                duplicates += 1

        self.uploading = False
        self._reload()
        yield _upload_feedback(created, duplicates, failures, warnings)

    @rx.event
    def scan_drop_folder(self):
        """Import every FIT file sitting in the drop folder."""
        self.loading = True
        yield

        with rx.session() as session:
            outcomes = scan_folder(session)

        self.loading = False
        self._reload()

        if not outcomes:
            yield notify_info("Drop folder is empty or missing.")
            return

        created = sum(1 for o in outcomes if o.created)
        failed = [o for o in outcomes if not o.ok]
        if failed:
            yield notify_error(
                f"Imported {created} ride(s); {len(failed)} file(s) could not be read."
            )
        else:
            yield notify_success(f"Imported {created} new ride(s) from the drop folder.")


def _to_card(ride: Ride, bike_names: dict[int | None, str]) -> RideCard:
    return RideCard(
        id=ride.id or 0,
        name=ride.name,
        date=ride.start_time.strftime("%d %b %Y, %H:%M"),
        distance_km=format_distance_km(ride.distance_m),
        elevation_m=format_number(ride.elevation_gain_m),
        duration=format_duration(ride.moving_time_s),
        bike_name=bike_names.get(ride.bike_id, "Unassigned"),
        preview_points=ride.preview_points,
        detail_href=f"{routes.RIDES_ROUTE}/{ride.id}",
    )


def _upload_feedback(
    created: int,
    duplicates: int,
    failures: list[str],
    warnings: list[str],
) -> Any:
    """Collapse a batch import into one honest toast.

    Duplicates and cross-check warnings are reported rather than hidden: a
    silent "imported 3 rides" when one of them disagreed with the device about
    normalized power is exactly the failure this milestone exists to catch.
    """
    if failures:
        return notify_error(
            failures[0] if len(failures) == 1 else f"{len(failures)} file(s) could not be imported."
        )
    if created and warnings:
        return notify_warning(
            f"Imported {created} ride(s), but {len(warnings)} value(s) disagreed "
            f"with the FIT file. First: {warnings[0]}"
        )
    if created:
        return notify_success(f"Imported {created} new ride(s).")
    if duplicates:
        return notify_info(f"Already imported ({duplicates} duplicate file(s)).")
    return notify_info("Nothing to import.")
