"""Routes list state (masterplan §2.3.1).

Flat state: subclasses ``rx.State`` directly, shared via
``await self.get_state(RoutesState)`` — which is how the ride-detail page
reaches the rename dialog after creating a route by hand.

Routes are the one entity the app derives entirely by itself: nothing in a FIT
file says "this is your commute". Everything on a card except the name is
computed from the rides that matched, so the name is the only editable field.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import reflex as rx
from sqlalchemy import func
from sqlmodel import col, select

from .. import metrics
from ..components.toast import notify_error, notify_success
from ..models import Ride, Route, RouteForm
from ..navigation import routes as route_urls
from ..utils.formatting import format_distance_km, format_duration, format_number
from ..utils.logging import get_logger
from ..utils.validation import parse_form

log = get_logger(__name__).bind(state="RoutesState")

# Placeholder name for a route created by hand from the ride-detail dropdown.
# Numbered plainly — "New route 2", not "New route (2)" — because these are
# placeholders awaiting a name, not disambiguated names like the ones
# `_unique_route_name` produces for auto-created routes.
NEW_ROUTE_NAME = "New route"


@dataclass
class RouteCard:
    """A route as shown on the Routes page — everything pre-formatted.

    Cards render inside ``rx.foreach``, where no per-item Python runs, so
    every string a card shows has to exist before the frontend sees it.
    """

    id: int = 0
    name: str = ""
    distance_km: str = ""
    elevation_m: str = ""
    duration: str = ""
    ride_count: int = 0
    ride_summary: str = ""
    last_ridden: str = ""
    preview_points: str = ""
    detail_href: str = ""


class RoutesState(rx.State):
    routes: list[RouteCard] = []

    dialog_open: bool = False
    editing_id: int | None = None
    form_errors: dict[str, str] = {}
    form_key: int = 0
    form_name: str = ""

    @rx.var
    def has_routes(self) -> bool:
        return len(self.routes) > 0

    @rx.var
    def route_count(self) -> int:
        return len(self.routes)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Page ``on_load`` — rebuild the card list."""
        self._reload()

    def _reload(self) -> None:
        with rx.session() as session:
            all_routes = session.exec(select(Route)).all()
            # One grouped query for the tiebreak, not one per card.
            latest = dict(
                session.exec(
                    select(col(Ride.route_id), func.max(col(Ride.start_time))).group_by(
                        col(Ride.route_id)
                    )
                ).all()
            )

        cards = [(route, latest.get(route.id)) for route in all_routes]
        # Most-ridden first — the list should answer "which are my regulars".
        # Ties go to whichever was ridden most recently.
        cards.sort(key=lambda pair: (pair[0].ride_count, pair[1] or ""), reverse=True)

        self.routes = [_to_card(route, last) for route, last in cards]
        log.info("routes.load", count=len(self.routes))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @rx.event
    def open_route(self, route_id: int):
        """Card click. Plain redirect rather than an ``rx.link`` wrapper.

        Wrapping the card in an anchor would make the rename pencil inside it
        unusable: ``stop_propagation`` halts bubbling but does not cancel the
        anchor's activation, so the click would navigate anyway.
        """
        return rx.redirect(f"{route_urls.ROUTES_ROUTE}/{route_id}")

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    @rx.event
    def open_rename(self, route_id: int):
        with rx.session() as session:
            route = session.get(Route, route_id)
            if route is None:
                log.warning("routes.rename.missing", route_id=route_id)
                return notify_error("That route no longer exists.")
            name = route.name

        self.prepare_rename(route_id, name)
        return None

    def prepare_rename(self, route_id: int, name: str) -> None:
        """Open the rename dialog on a route, prefilled.

        A plain method, not an event handler, so another state can drive it
        through ``get_state`` — which is how ride detail hands over after
        creating a route by hand.
        """
        self.editing_id = route_id
        self.form_errors = {}
        self.form_name = name
        self.form_key += 1
        self.dialog_open = True

    @rx.event
    def set_dialog_open(self, value: bool):
        """Bound to ``on_open_change`` so Escape and click-outside also clear."""
        self.dialog_open = value
        if not value:
            self.form_errors = {}

    @rx.event
    def submit_rename(self, form_data: dict[str, Any]):
        """Validate and persist the new name.

        On failure the handler returns early, leaving ``dialog_open`` True so
        the user keeps their input and sees the message.
        """
        if self.editing_id is None:
            self.dialog_open = False
            return None

        parsed, errors = parse_form(form_data, RouteForm)
        if errors or parsed is None:
            self.form_errors = errors
            self.form_name = str(form_data.get("name", ""))
            return None

        with rx.session() as session:
            route = session.get(Route, self.editing_id)
            if route is None:
                self.dialog_open = False
                return notify_error("That route no longer exists.")

            # Uniqueness needs a session, so it cannot live on the form model.
            # Deliberately *reject* rather than reusing `_unique_route_name`:
            # silently turning the typed "Commute" into "Commute (2)" reads as
            # the app ignoring the input.
            clash = session.exec(
                select(Route)
                .where(Route.name == parsed.name)
                .where(col(Route.id) != self.editing_id)
            ).first()
            if clash is not None:
                self.form_errors = {"name": "Another route already has that name."}
                self.form_name = parsed.name
                return None

            route.name = parsed.name
            session.add(route)
            session.commit()
            name = parsed.name

        log.info("routes.rename", route_id=self.editing_id, name=name)
        self.dialog_open = False
        self.editing_id = None
        self._reload()
        return notify_success(f"Renamed to “{name}”.")


# ---------------------------------------------------------------------------
# View-model mapping
# ---------------------------------------------------------------------------


def _to_card(route: Route, last_ridden: Any) -> RouteCard:
    return RouteCard(
        id=route.id or 0,
        name=route.name,
        distance_km=format_distance_km(route.avg_distance_m),
        elevation_m=format_number(route.avg_elevation_gain_m),
        duration=format_duration(route.avg_moving_time_s),
        ride_count=route.ride_count,
        ride_summary=(
            "Ridden once" if route.ride_count == 1 else f"Ridden {route.ride_count} times"
        ),
        last_ridden=_format_last(last_ridden),
        # Projected per load rather than stored: `route.points` is already
        # simplified to a few hundred points and the projection thins to 120,
        # so this is sub-millisecond and touches no stream. Only the *ride*
        # tracks needed precomputing.
        preview_points=metrics.project_svg_points([(p[0], p[1]) for p in route.points]),
        detail_href=f"{route_urls.ROUTES_ROUTE}/{route.id}",
    )


def _format_last(value: Any) -> str:
    """The grouped ``max(start_time)`` comes back as a string on SQLite."""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")

    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return text[:10]
