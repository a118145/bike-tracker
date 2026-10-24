"""Rides list state (masterplan §2.4.1).

Flat state: subclasses ``rx.State`` directly, and other states reach it with
``await self.get_state(RidesState)`` rather than through inheritance.

The list is built as pre-formatted view models rather than raw ``Ride`` rows.
Cards render inside ``rx.foreach``, where no per-item Python runs, so every
string a card shows — the distance, the duration, the thumbnail geometry —
has to exist before the frontend sees it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import reflex as rx
from sqlmodel import col, select

from ..components.toast import notify_error, notify_info, notify_success, notify_warning
from ..ingest.sources.file import import_bytes, scan_folder
from ..models import EVAL_FTP, Bike, FitnessEval, Ride, excluded_ride_ids
from ..navigation import routes
from ..utils.formatting import (
    format_distance_km,
    format_duration,
    format_number,
)
from ..utils.logging import get_logger
from .tags_state import TagPill, all_tags, load_ride_tags, to_pill

log = get_logger(__name__).bind(state="RidesState")

# Cap on a single upload. A FIT activity is a few hundred kilobytes; anything
# far larger is not a ride, and decoding it would block the event loop.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@dataclass
class RideEvalBadge:
    """An evaluation this ride produced, as shown on its card.

    Colours match the badges on the Power card in ride detail
    (``components/ride_stats.py``) so the same evaluation looks the same
    wherever you meet it. Only the label travels: the value belongs on the
    detail page, and a 360 px card has no room for "FTP 285 W".
    """

    label: str = ""
    color_scheme: str = ""


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
    # An explicit flag rather than comparing `bike_name` against the fallback
    # string at the call site: that would couple the component to a default
    # set here, and break silently if either changed.
    bike_assigned: bool = False
    evaluations: list[RideEvalBadge] = field(default_factory=list)
    preview_points: str = ""
    detail_href: str = ""
    # Excluded from analysis (AGENTS.md rule 42): still listed, but dimmed.
    excluded: bool = False
    tags: list[TagPill] = field(default_factory=list)
    tag_ids: list[int] = field(default_factory=list)


class RidesState(rx.State):
    rides: list[RideCard] = []
    loading: bool = False
    uploading: bool = False

    # Tag filter: every defined tag as a toggle chip, and the selected ids.
    # Kept across navigation like the dashboard's bike filter.
    filter_tags: list[TagPill] = []
    selected_tags: list[int] = []

    @rx.var
    def has_rides(self) -> bool:
        return len(self.rides) > 0

    @rx.var
    def ride_count(self) -> int:
        return len(self.rides)

    @rx.var
    def visible_rides(self) -> list[RideCard]:
        return filter_by_tags(self.rides, self.selected_tags)

    @rx.var
    def filtering(self) -> bool:
        return len(self.selected_tags) > 0

    @rx.var
    def count_text(self) -> str:
        if not self.selected_tags:
            return f"{len(self.rides)} ride(s) recorded."
        shown = len(filter_by_tags(self.rides, self.selected_tags))
        return f"{shown} of {len(self.rides)} ride(s) shown."

    @rx.event
    def toggle_tag(self, tag_id: int):
        if tag_id in self.selected_tags:
            self.selected_tags = [t for t in self.selected_tags if t != tag_id]
        else:
            self.selected_tags = [*self.selected_tags, tag_id]

    @rx.event
    def clear_tag_filter(self):
        self.selected_tags = []

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
            # One query for the whole list, not one per card.
            evaluations = session.exec(
                select(FitnessEval).order_by(col(FitnessEval.created_at))
            ).all()
            tags = load_ride_tags(session)
            excluded = excluded_ride_ids(session, (r.id for r in rides if r.id is not None))
            self.filter_tags = [to_pill(tag) for tag in all_tags(session)]

        # A tag deleted in Settings must not linger as an invisible filter
        # that hides every ride.
        known = {pill.id for pill in self.filter_tags}
        self.selected_tags = [t for t in self.selected_tags if t in known]

        by_ride = group_evaluations(evaluations)
        self.rides = [_to_card(ride, bike_names, by_ride, tags, excluded) for ride in rides]
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


def group_evaluations(records: Sequence[FitnessEval]) -> dict[int, list[RideEvalBadge]]:
    """Badges per ride id, for the whole list in one pass.

    An evaluation with no ``ride_id`` — a hand-entered LTHR from the Fitness
    page — belongs to no ride and is dropped here. Filtering in the query
    instead would put the rule somewhere no test can reach it, and grouping
    such a record under ``ride_id or 0`` would silently invent a ride.
    """
    grouped: dict[int, list[RideEvalBadge]] = {}
    for record in records:
        if record.ride_id is None:
            continue
        grouped.setdefault(record.ride_id, []).append(_to_badge(record))
    return grouped


def filter_by_tags(cards: Sequence[RideCard], selected: Sequence[int]) -> list[RideCard]:
    """The cards carrying **any** of the selected tags; all of them if none is."""
    if not selected:
        return list(cards)
    wanted = set(selected)
    return [card for card in cards if wanted.intersection(card.tag_ids)]


def _to_badge(record: FitnessEval) -> RideEvalBadge:
    """Same label and colour rule as ``ride_detail_state._load_badges``."""
    if record.type == EVAL_FTP:
        return RideEvalBadge(label="FTP", color_scheme="amber")
    return RideEvalBadge(label="Baseline", color_scheme="teal")


def _to_card(
    ride: Ride,
    bike_names: dict[int | None, str],
    evaluations: dict[int, list[RideEvalBadge]],
    tags: dict[int, list[TagPill]] | None = None,
    excluded: set[int] | None = None,
) -> RideCard:
    pills = (tags or {}).get(ride.id or 0, [])
    return RideCard(
        id=ride.id or 0,
        name=ride.name,
        # Date only. The card is 360 px and the date now shares the title row,
        # so the time would cost the name a third of its width; it is still on
        # the detail page.
        date=ride.start_time.strftime("%d %b %Y"),
        distance_km=format_distance_km(ride.distance_m),
        elevation_m=format_number(ride.elevation_gain_m),
        duration=format_duration(ride.moving_time_s),
        bike_name=bike_names.get(ride.bike_id, "Unassigned"),
        bike_assigned=ride.bike_id in bike_names,
        evaluations=evaluations.get(ride.id or 0, []),
        preview_points=ride.preview_points,
        detail_href=f"{routes.RIDES_ROUTE}/{ride.id}",
        excluded=ride.id in (excluded or set()),
        tags=pills,
        tag_ids=[pill.id for pill in pills],
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
