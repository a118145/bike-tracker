"""Ride detail (masterplan §2.4.2).

Layout order, top to bottom, is the spec:

1. heading, back link, bike dropdown, route dropdown, Fitness menu, note button
2. tags — pills plus an input that suggests the tags defined in Settings
3. full-page map
4. y-t chart — elevation always, extra channels toggled by the stat cards
5. lap bars, when the ride has more than one lap
6. grouped stat cards matching ``docs/image.png``

The page has no nav entry of its own; ``NavState.active_route`` resolves
``/rides/5`` to ``/rides`` and keeps the sidebar highlighting Rides.
"""

import reflex as rx

from ..components.fitness_dialog import fitness_dialog, fitness_menu
from ..components.hover_cursor import hover_cursor_bridge
from ..components.lap_bars import lap_bars
from ..components.map_view import map_slot
from ..components.note_dialog import note_button, note_dialog
from ..components.ride_chart import chart_skeleton, ride_chart
from ..components.ride_stats import ride_stat_cards
from ..components.route_dialog import route_dialog
from ..components.tags import tag_pill
from ..navigation import routes
from ..navigation.nav_config import label_for_route
from ..states.ride_detail_state import (
    CREATE_ROUTE_OPTION,
    NONE_OPTION,
    RideDetailState,
    SelectOption,
)
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
    """One dropdown entry.

    The last route entry is the "Create new" action rather than a route, and
    is italicised so it does not read as one.
    """
    return rx.select.item(
        rx.text(
            item.label,
            font_style=rx.cond(item.value == CREATE_ROUTE_OPTION, "italic", "normal"),
        ),
        value=rx.cond(item.value == "", NONE_OPTION, item.value),
    )


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


# Ties the input to its suggestion list. Must be unique on the page.
_TAG_DATALIST_ID = "ride-tag-options"


def _tag_row() -> rx.Component:
    """The ride's tags, and an input to attach another (AGENTS.md rule 43).

    Suggestions come from a native ``<datalist>``: the browser filters and
    shows them without a round trip per keystroke, and without a line of
    custom JS (rule 23). Enter attaches what was typed; only tags defined in
    Settings are accepted.

    Gated on ``stale`` like the rest of the page: on a client-side navigation
    the previous ride's pills would flash, and an Enter or an ``x`` would
    write to the ride being loaded.
    """
    return rx.hstack(
        rx.icon("tag", size=15, color=rx.color("gray", 10)),
        rx.skeleton(
            rx.hstack(
                rx.foreach(
                    RideDetailState.ride_tags,
                    lambda pill: tag_pill(
                        pill,
                        size="2",
                        on_remove=RideDetailState.remove_tag,
                        disabled=RideDetailState.stale,
                    ),
                ),
                spacing="2",
                align="center",
                wrap="wrap",
            ),
            loading=RideDetailState.stale,
        ),
        rx.input(
            placeholder="Add tag…",
            value=RideDetailState.tag_query,
            on_change=RideDetailState.set_tag_query,
            on_key_down=RideDetailState.handle_tag_key,
            disabled=RideDetailState.stale,
            size="1",
            variant="soft",
            width="10em",
            custom_attrs={"list": _TAG_DATALIST_ID, "autoComplete": "off"},
        ),
        rx.el.datalist(
            rx.foreach(RideDetailState.tag_suggestions, lambda name: rx.el.option(value=name)),
            id=_TAG_DATALIST_ID,
        ),
        spacing="2",
        align="center",
        wrap="wrap",
        width="100%",
    )


def _header() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            # Skeletons rather than the text: on a client-side navigation the
            # page first renders with the *previous* ride's title, because
            # Reflex does not reset state and the new data is a round-trip
            # away. See RideDetailState.stale.
            rx.skeleton(
                rx.heading(RideDetailState.title, size="7"),
                loading=RideDetailState.stale,
            ),
            rx.skeleton(
                rx.text(RideDetailState.subtitle, size="2", color_scheme="gray"),
                loading=RideDetailState.stale,
            ),
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
            fitness_menu(),
            note_button(),
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
            rx.cond(
                RideDetailState.stale,
                chart_skeleton(),
                ride_chart(RideDetailState.chart_figure),
            ),
            lap_bars(),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _found() -> rx.Component:
    return rx.vstack(
        _header(),
        _tag_row(),
        map_slot(
            RideDetailState.map_figure,
            loading=RideDetailState.stale,
            has_track=RideDetailState.has_track,
            record_key=RideDetailState.map_key,
        ),
        _chart(),
        rx.skeleton(ride_stat_cards(), loading=RideDetailState.stale),
        # Picking "Create new" from the route dropdown makes a route and hands
        # straight to this dialog, so it has to be mounted here as well as on
        # the routes list.
        route_dialog(),
        fitness_dialog(),
        note_dialog(),
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
            # Installs the client-side chart-to-map hover cursor. Renders
            # nothing; it only needs to be on the page.
            hover_cursor_bridge(),
            _back_link(),
            rx.cond(RideDetailState.not_found, _missing(), _found()),
            spacing="4",
            align="start",
            width="100%",
        )
    )
