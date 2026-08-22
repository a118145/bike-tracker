"""Routes list (masterplan §2.3.1).

A card per derived route: map preview as a borderless inset, name, distance,
elevation, average ride time and how often it was ridden. Most-ridden first,
so the list answers "which are my regular routes" rather than "what did I do
last".

Nothing here creates a route. They appear when an imported ride matches
nothing on record, and vanish when their last ride is reassigned away — the
only thing a user sets is the name.
"""

import reflex as rx

from ..components.cards import card_grid
from ..components.map_view import route_preview_svg
from ..components.route_dialog import route_dialog
from ..navigation import routes as route_urls
from ..states.routes_state import RouteCard, RoutesState
from ..ui.base import base_page


def _figure(value: rx.Var[str], unit: str, label: str) -> rx.Component:
    """One compact statistic. Labels are terse because the card is 360 px."""
    return rx.vstack(
        rx.text(label, size="1", color_scheme="gray", white_space="nowrap"),
        rx.hstack(
            rx.text(value, size="3", weight="bold"),
            rx.text(unit, size="1", color_scheme="gray", padding_bottom="0.1em"),
            spacing="1",
            align="end",
        ),
        spacing="0",
        align="start",
        min_width="0",
    )


def _route_card(route: RouteCard) -> rx.Component:
    return rx.card(
        rx.hstack(
            route_preview_svg(route.preview_points, size="64px"),
            rx.vstack(
                rx.hstack(
                    rx.heading(route.name, size="3", white_space="nowrap", overflow="hidden"),
                    # The pencil belongs against the name it edits, not adrift
                    # at the far edge of the card. At 360 px the badge below
                    # wraps under this row, which keeps the pairing obvious.
                    #
                    # `.stop_propagation` works here only because the card is a
                    # box with an `on_click`; inside an `rx.link` it would halt
                    # bubbling without cancelling the anchor's navigation.
                    rx.icon_button(
                        rx.icon("pencil", size=14),
                        variant="ghost",
                        color_scheme="gray",
                        title="Rename route",
                        on_click=RoutesState.open_rename(route.id).stop_propagation,
                    ),
                    align="center",
                    spacing="1",
                    width="100%",
                    min_width="0",
                ),
                rx.hstack(
                    rx.badge(route.ride_summary, variant="soft", color_scheme="gray"),
                    rx.text(route.last_ridden, size="1", color_scheme="gray"),
                    spacing="2",
                    align="center",
                    wrap="wrap",
                ),
                rx.hstack(
                    _figure(route.distance_km, "km", "Dist"),
                    _figure(route.elevation_m, "m", "Asc"),
                    _figure(route.duration, "", "Time"),
                    spacing="4",
                    width="100%",
                    padding_top="0.35em",
                ),
                spacing="1",
                align="start",
                width="100%",
                min_width="0",
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        width="100%",
        cursor="pointer",
        on_click=RoutesState.open_route(route.id),
        style={"_hover": {"borderColor": rx.color("accent", 8)}},
    )


def _empty_state() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.icon("route", size=32, color=rx.color("gray", 8)),
            rx.text("No routes yet.", weight="medium"),
            rx.text(
                "Routes appear on their own: each imported ride is matched "
                "against the routes on record, and starts a new one when "
                "nothing fits. Import a ride to see the first.",
                size="2",
                color_scheme="gray",
                text_align="center",
                max_width="28em",
            ),
            spacing="2",
            align="center",
        ),
        padding_y="3em",
        width="100%",
    )


@rx.page(route=route_urls.ROUTES_ROUTE, title="Routes", on_load=RoutesState.load)
def routes_page() -> rx.Component:
    return base_page(
        rx.vstack(
            rx.vstack(
                rx.heading("Routes", size="7"),
                rx.text(
                    rx.cond(
                        RoutesState.has_routes,
                        RoutesState.route_count.to_string() + " route(s) derived from your rides.",
                        "Nothing derived yet.",
                    ),
                    size="2",
                    color_scheme="gray",
                ),
                spacing="1",
                align="start",
            ),
            rx.cond(
                RoutesState.has_routes,
                card_grid(rx.foreach(RoutesState.routes, _route_card)),
                _empty_state(),
            ),
            route_dialog(),
            spacing="4",
            width="100%",
            align="start",
        )
    )
