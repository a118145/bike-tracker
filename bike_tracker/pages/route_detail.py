"""Route detail (masterplan §2.3.2).

Layout order, top to bottom, is the spec:

1. heading and back link
2. page-wide map, every matched ride overlaid so deviations show
3. page-wide card of route-level data
4. the table of matching rides, each linking to its ride detail

The page has no nav entry of its own; ``NavState.active_route`` resolves
``/routes/3`` to ``/routes`` and keeps the sidebar highlighting Routes.
"""

import reflex as rx

from ..components.map_view import empty_map, route_map
from ..components.route_rides_table import route_rides_table
from ..navigation import routes as route_urls
from ..navigation.nav_config import label_for_route
from ..states.route_detail_state import RouteDetailState
from ..ui.base import base_page

# Read from the nav config so the back link names whatever the menu calls this
# section. Resolved at compile time — it is a plain Python constant.
_PARENT_LABEL = label_for_route(route_urls.ROUTES_ROUTE)


def _back_link() -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.icon("arrow-left", size=15),
            rx.text(f"Back to {_PARENT_LABEL}", size="2"),
            align="center",
            spacing="1",
        ),
        href=route_urls.ROUTES_ROUTE,
        underline="none",
        color_scheme="gray",
    )


def _stat(label: str, key: str, unit: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(label, size="1", color_scheme="gray", white_space="nowrap"),
        rx.hstack(
            rx.heading(RouteDetailState.stats.get(key, "—"), size="5", weight="medium"),
            rx.text(
                unit,
                size="1",
                color_scheme="gray",
                weight="bold",
                padding_bottom="0.15em",
            ),
            spacing="1",
            align="end",
        ),
        spacing="0",
        align="start",
        min_width="0",
    )


def _summary_card() -> rx.Component:
    """Route-level data — what does not change meaningfully between rides."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("route", size=16, color=rx.color("accent", 10)),
                rx.text("Route", size="2", weight="bold"),
                align="center",
                spacing="2",
                width="100%",
            ),
            rx.grid(
                _stat("Ø Length", "distance", "km"),
                _stat("Ø Ascent", "elevation", "m"),
                _stat("Ø Ride time", "avg_time"),
                _stat("Ridden", "ride_count", "times"),
                _stat("Fastest", "fastest"),
                _stat("Fastest on", "fastest_on"),
                _stat("Ø TSS", "avg_tss"),
                _stat("Best TSS", "best_tss"),
                columns="4",
                spacing="4",
                width="100%",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def _found() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            rx.heading(RouteDetailState.title, size="7"),
            rx.text(RouteDetailState.subtitle, size="2", color_scheme="gray"),
            spacing="1",
            align="start",
        ),
        rx.cond(
            RouteDetailState.has_track,
            route_map(RouteDetailState.map_figure),
            empty_map("No GPS track on this route's rides."),
        ),
        rx.cond(
            RouteDetailState.truncated_note != "",
            rx.text(
                RouteDetailState.truncated_note,
                size="1",
                color_scheme="gray",
            ),
        ),
        _summary_card(),
        route_rides_table(),
        spacing="4",
        align="start",
        width="100%",
    )


def _missing() -> rx.Component:
    return rx.callout(
        "No route with that id. A route is removed when its last ride is assigned elsewhere.",
        icon="search-x",
        color_scheme="red",
    )


@rx.page(
    route=route_urls.ROUTE_DETAIL_ROUTE,
    title="Route",
    on_load=RouteDetailState.load,
)
def route_detail() -> rx.Component:
    return base_page(
        rx.vstack(
            _back_link(),
            rx.cond(RouteDetailState.not_found, _missing(), _found()),
            spacing="4",
            align="start",
            width="100%",
        )
    )
