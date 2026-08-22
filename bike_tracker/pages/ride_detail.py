"""Ride detail (masterplan §2.4.2).

Layout order, top to bottom, is the spec:

1. heading, back link, bike dropdown, route dropdown
2. full-page map
3. y-t chart — elevation always, extra channels toggled by the stat cards
4. lap bars, when the ride has more than one lap
5. grouped stat cards matching ``docs/image.png``

The page has no nav entry of its own; ``NavState.active_route`` resolves
``/rides/5`` to ``/rides`` and keeps the sidebar highlighting Rides.
"""

import reflex as rx

from ..components.lap_bars import lap_bars
from ..components.map_view import empty_map, route_map
from ..components.ride_chart import ride_chart
from ..components.ride_stats import ride_stat_cards
from ..navigation import routes
from ..navigation.nav_config import label_for_route
from ..states.ride_detail_state import NONE_OPTION, RideDetailState, SelectOption
from ..ui.base import base_page

# Read from the nav config so the back link names whatever the menu calls this
# section. Resolved at compile time — it is a plain Python constant.
_PARENT_LABEL = label_for_route(routes.RIDES_ROUTE)


def _back_link() -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.icon("arrow-left", size=15),
            rx.text(f"Back to {_PARENT_LABEL}", size="2"),
            align="center",
            spacing="1",
        ),
        href=routes.RIDES_ROUTE,
        underline="none",
        color_scheme="gray",
    )


def _option(item: SelectOption) -> rx.Component:
    return rx.select.item(item.label, value=rx.cond(item.value == "", NONE_OPTION, item.value))


def _selector(
    icon: str,
    options: rx.Var[list[SelectOption]],
    value: rx.Var[str],
    on_change: rx.EventHandler,
    placeholder: str,
) -> rx.Component:
    """A labelled dropdown for the bike / route association.

    Radix's select rejects an empty string as an item value, so "unassigned"
    travels as a sentinel and is mapped back in the state.
    """
    return rx.hstack(
        rx.icon(icon, size=15, color=rx.color("gray", 10)),
        rx.select.root(
            rx.select.trigger(placeholder=placeholder, variant="soft"),
            rx.select.content(rx.foreach(options, _option)),
            value=rx.cond(value == "", NONE_OPTION, value),
            on_change=on_change,
        ),
        align="center",
        spacing="2",
    )


def _header() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.heading(RideDetailState.title, size="7"),
            rx.text(RideDetailState.subtitle, size="2", color_scheme="gray"),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.hstack(
            _selector(
                "bike",
                RideDetailState.bike_options,
                RideDetailState.bike_value,
                RideDetailState.set_bike,
                "Bike",
            ),
            _selector(
                "route",
                RideDetailState.route_options,
                RideDetailState.route_value,
                RideDetailState.set_route,
                "Route",
            ),
            spacing="4",
            align="center",
            wrap="wrap",
        ),
        width="100%",
        align="center",
        spacing="4",
        wrap="wrap",
    )


def _chart() -> rx.Component:
    return rx.card(
        rx.vstack(
            ride_chart(RideDetailState.chart_figure),
            lap_bars(),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _found() -> rx.Component:
    return rx.vstack(
        _header(),
        rx.cond(
            RideDetailState.has_track,
            route_map(RideDetailState.map_figure),
            empty_map(),
        ),
        _chart(),
        ride_stat_cards(),
        spacing="4",
        align="start",
        width="100%",
    )


def _missing() -> rx.Component:
    return rx.callout("No ride with that id.", icon="search-x", color_scheme="red")


@rx.page(route=routes.RIDE_DETAIL_ROUTE, title="Ride", on_load=RideDetailState.load)
def ride_detail() -> rx.Component:
    return base_page(
        rx.vstack(
            _back_link(),
            rx.cond(RideDetailState.not_found, _missing(), _found()),
            spacing="4",
            align="start",
            width="100%",
        )
    )
