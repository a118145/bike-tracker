"""Tags: their definitions in Settings, and the pill view model every page shares.

Tags are global (AGENTS.md rule 43). They are created, renamed, recoloured and
deleted only here; ride detail attaches existing ones. What a tag *does* is its
``effect``, so the two edits that can change the set of rides in analysis —
switching an effect, deleting an excluding tag — refresh the statistics of
every route the tag's rides sit on.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import reflex as rx
from sqlmodel import Session, col, func, select

from ..components.toast import notify_error, notify_success
from ..ingest.importer import refresh_routes_for_rides
from ..models import TAG_COLORS, TAG_EFFECT_EXCLUDE, TAG_EFFECTS, RideTag, Tag, TagForm
from ..utils.logging import get_logger
from ..utils.validation import parse_form

log = get_logger(__name__).bind(state="TagsState")

# Radix select rejects "" as an item value, so "no effect" travels as this and
# is mapped back before validation — same trick as ride detail's NONE_OPTION.
NO_EFFECT = "__none__"


@dataclass
class TagPill:
    """A tag as drawn on a ride: everything a pill needs, nothing more."""

    id: int = 0
    name: str = ""
    color: str = "gray"
    # The pill gets an eye-off icon, so an excluded ride says why at a glance.
    excludes: bool = False


@dataclass
class TagRow:
    """One tag in the Settings list."""

    id: int = 0
    pill: TagPill = field(default_factory=TagPill)
    effect_label: str = ""
    ride_summary: str = ""


@dataclass
class EffectOption:
    value: str = ""
    label: str = ""


EFFECT_OPTIONS = [
    EffectOption(value=NO_EFFECT, label="No effect"),
    *(EffectOption(value=value, label=label) for value, label in TAG_EFFECTS.items()),
]


def to_pill(tag: Tag) -> TagPill:
    return TagPill(
        id=tag.id or 0,
        name=tag.name,
        # Guards `color_scheme` against a value written outside the form.
        color=tag.color if tag.color in TAG_COLORS else "gray",
        excludes=tag.effect == TAG_EFFECT_EXCLUDE,
    )


def group_tags(rows: Iterable[tuple[int, Tag]]) -> dict[int, list[TagPill]]:
    """Pills per ride id, from ``(ride_id, tag)`` pairs, each list sorted by name.

    Sorted here rather than in the query so the order is the same wherever
    pills are drawn, whichever query fed them.
    """
    grouped: dict[int, list[TagPill]] = {}
    for ride_id, tag in rows:
        grouped.setdefault(ride_id, []).append(to_pill(tag))
    for pills in grouped.values():
        pills.sort(key=lambda pill: pill.name.casefold())
    return grouped


def load_ride_tags(
    session: Session, ride_ids: Iterable[int] | None = None
) -> dict[int, list[TagPill]]:
    """Every ride's tags in one query — for a list, not one query per card.

    ``ride_ids=None`` means all rides.
    """
    query = select(RideTag.ride_id, Tag).join(Tag, col(Tag.id) == col(RideTag.tag_id))
    if ride_ids is not None:
        query = query.where(col(RideTag.ride_id).in_(list(ride_ids)))
    return group_tags(session.exec(query).all())


def all_tags(session: Session) -> Sequence[Tag]:
    return session.exec(select(Tag).order_by(func.lower(Tag.name))).all()


def find_tag_by_name(session: Session, name: str) -> Tag | None:
    """Case-insensitive lookup, so "Commute" and "commute" are one tag."""
    return session.exec(select(Tag).where(func.lower(Tag.name) == name.strip().lower())).first()


def tag_ride_ids(session: Session, tag_id: int) -> list[int]:
    return list(session.exec(select(RideTag.ride_id).where(RideTag.tag_id == tag_id)).all())


class TagsState(rx.State):
    tags: list[TagRow] = []

    dialog_open: bool = False
    confirm_open: bool = False

    editing_id: int | None = None
    pending_delete_id: int | None = None
    pending_delete_name: str = ""
    pending_delete_rides: int = 0

    form_errors: dict[str, str] = {}
    form_key: int = 0
    form_name: str = ""
    form_color: str = "gray"
    form_effect: str = NO_EFFECT

    @rx.var
    def dialog_title(self) -> str:
        return "Edit tag" if self.editing_id is not None else "New tag"

    @rx.var
    def submit_label(self) -> str:
        return "Save changes" if self.editing_id is not None else "Create"

    @rx.var
    def has_tags(self) -> bool:
        return len(self.tags) > 0

    @rx.var
    def delete_description(self) -> str:
        """Say exactly what the delete will cost before it happens."""
        if self.pending_delete_rides == 0:
            return "No ride carries this tag."
        return (
            f"{self.pending_delete_rides} ride(s) will lose this tag. "
            "The rides themselves are kept."
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Page ``on_load`` — rebuild the tag list."""
        self._reload()

    def _reload(self) -> None:
        with rx.session() as session:
            tags = all_tags(session)
            counts = dict(
                session.exec(
                    select(RideTag.tag_id, func.count()).group_by(col(RideTag.tag_id))
                ).all()
            )
        self.tags = [_to_row(tag, counts.get(tag.id or 0, 0)) for tag in tags]
        log.info("tags.load", count=len(self.tags))

    # ------------------------------------------------------------------
    # Dialog
    # ------------------------------------------------------------------

    @rx.event
    def open_create(self):
        self.editing_id = None
        self.form_errors = {}
        self.form_name = ""
        self.form_color = "gray"
        self.form_effect = NO_EFFECT
        self.form_key += 1
        self.dialog_open = True

    @rx.event
    def open_edit(self, tag_id: int):
        with rx.session() as session:
            tag = session.get(Tag, tag_id)
            if tag is None:
                log.warning("tags.edit.missing", tag_id=tag_id)
                return notify_error("That tag no longer exists.")

        self.editing_id = tag_id
        self.form_errors = {}
        self.form_name = tag.name
        self.form_color = tag.color
        self.form_effect = tag.effect or NO_EFFECT
        self.form_key += 1
        self.dialog_open = True
        return None

    @rx.event
    def set_dialog_open(self, value: bool):
        """Bound to ``on_open_change`` so Escape and click-outside also clear."""
        self.dialog_open = value
        if not value:
            self.form_errors = {}

    @rx.event
    def submit_form(self, form_data: dict[str, Any]):
        """Cast, validate, persist; refresh routes if the effect changed.

        On failure the handler returns early, which leaves ``dialog_open``
        True so the user keeps their input and sees per-field messages.
        """
        raw = dict(form_data)
        if raw.get("effect") == NO_EFFECT:
            raw["effect"] = ""
        parsed, errors = parse_form(raw, TagForm)

        if parsed is not None and not errors:
            with rx.session() as session:
                clash = find_tag_by_name(session, parsed.name)
                if clash is not None and clash.id != self.editing_id:
                    errors = {"name": f"A tag called “{clash.name}” already exists."}

        if errors or parsed is None:
            self.form_errors = errors
            # Keep what was typed so the remount does not wipe it.
            self.form_name = str(form_data.get("name", ""))
            self.form_color = str(form_data.get("color", "gray"))
            self.form_effect = str(form_data.get("effect", NO_EFFECT))
            log.info("tags.submit.invalid", fields=sorted(errors))
            return None

        self.form_errors = {}
        with rx.session() as session:
            if self.editing_id is None:
                tag = Tag(**parsed.model_dump())
                session.add(tag)
                session.commit()
                session.refresh(tag)
                tag_id, name = tag.id or 0, tag.name
                effect_changed = False
                message = f"Created “{name}”."
            else:
                existing = session.get(Tag, self.editing_id)
                if existing is None:
                    self.dialog_open = False
                    log.warning("tags.update.missing", tag_id=self.editing_id)
                    return notify_error("That tag no longer exists.")
                effect_changed = existing.effect != parsed.effect
                for field_name, value in parsed.model_dump().items():
                    setattr(existing, field_name, value)
                session.add(existing)
                # Read before the commit expires the instance (rule 24).
                tag_id, name = existing.id or 0, existing.name
                session.commit()
                message = f"Updated “{name}”."

            refreshed = 0
            if effect_changed:
                refreshed = refresh_routes_for_rides(session, tag_ride_ids(session, tag_id))

        log.info(
            "tag.create" if self.editing_id is None else "tag.update",
            tag_id=tag_id,
            effect=parsed.effect,
            routes_refreshed=refreshed,
        )
        self.dialog_open = False
        self.editing_id = None
        self._reload()
        return notify_success(message)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    @rx.event
    def ask_delete(self, tag_id: int):
        with rx.session() as session:
            tag = session.get(Tag, tag_id)
            if tag is None:
                return notify_error("That tag no longer exists.")
            ride_count = len(tag_ride_ids(session, tag_id))

        self.pending_delete_id = tag_id
        self.pending_delete_name = tag.name
        self.pending_delete_rides = ride_count
        self.confirm_open = True
        return None

    @rx.event
    def set_confirm_open(self, value: bool):
        self.confirm_open = value
        if not value:
            self.pending_delete_id = None

    @rx.event
    def confirm_delete(self):
        """Take the tag off every ride, then delete it.

        The links go first: SQLite does not enforce the foreign key here, so a
        dangling ``RideTag`` would survive and keep excluding a ride through a
        tag nobody can see. If the tag excluded rides, they are back in
        analysis and their routes' averages are recomputed.
        """
        tag_id = self.pending_delete_id
        self.confirm_open = False
        self.pending_delete_id = None
        if tag_id is None:
            return None

        with rx.session() as session:
            tag = session.get(Tag, tag_id)
            if tag is None:
                return notify_error("That tag no longer exists.")
            name, excluded = tag.name, tag.effect == TAG_EFFECT_EXCLUDE

            links = session.exec(select(RideTag).where(RideTag.tag_id == tag_id)).all()
            ride_ids = [link.ride_id for link in links]
            for link in links:
                session.delete(link)
            session.delete(tag)
            session.commit()

            refreshed = refresh_routes_for_rides(session, ride_ids) if excluded else 0

        log.info("tag.delete", tag_id=tag_id, rides=len(ride_ids), routes_refreshed=refreshed)
        self._reload()
        return notify_success(
            f"Deleted “{name}”."
            if not ride_ids
            else f"Deleted “{name}”; removed from {len(ride_ids)} ride(s)."
        )


def _to_row(tag: Tag, ride_count: int) -> TagRow:
    return TagRow(
        id=tag.id or 0,
        pill=to_pill(tag),
        effect_label=TAG_EFFECTS.get(tag.effect or "", ""),
        ride_summary=f"{ride_count} ride" if ride_count == 1 else f"{ride_count} rides",
    )
