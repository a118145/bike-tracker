"""Every ride on one route, newest first (masterplan §2.3.2).

The columns are deliberately all the things that *differ* between two passes
of the same course: the route is fixed, so ride time, power and load are the
rider changing rather than the terrain. Distance and elevation are absent for
the same reason — they belong on the summary card above, where they are
stated once.

Kept route-specific on purpose, like the scaffold's table it is modelled on: a
generic table over arbitrary models needs column descriptors and per-cell
renderers, which is more machinery than nine fixed columns justify.
"""

import reflex as rx

from ..states.route_detail_state import RideRow, RouteDetailState


def _row(ride: RideRow) -> rx.Component:
    return rx.table.row(
        rx.table.row_header_cell(
            rx.link(ride.date, href=ride.detail_href, underline="always"),
        ),
        rx.table.cell(ride.duration),
        rx.table.cell(ride.avg_speed),
        rx.table.cell(ride.avg_power),
        rx.table.cell(ride.normalized_power),
        rx.table.cell(ride.best_20min),
        rx.table.cell(ride.intensity_factor),
        rx.table.cell(ride.tss),
        rx.table.cell(ride.avg_hr),
        align="center",
        # Excluded from analysis: still listed, but visibly not counted in
        # the summary card or the map above.
        opacity=rx.cond(ride.excluded, "0.45", "1"),
        title=rx.cond(ride.excluded, "Excluded from analysis", ""),
    )


def _header() -> rx.Component:
    return rx.table.header(
        rx.table.row(
            rx.table.column_header_cell("Date"),
            rx.table.column_header_cell("Ride time"),
            rx.table.column_header_cell("Ø Speed"),
            rx.table.column_header_cell("Ø Power"),
            rx.table.column_header_cell("NP"),
            rx.table.column_header_cell("Best 20 min"),
            rx.table.column_header_cell("IF"),
            rx.table.column_header_cell("TSS"),
            rx.table.column_header_cell("Ø HR"),
        ),
    )


def _empty_state() -> rx.Component:
    """Should be unreachable: a route with no rides is deleted.

    Kept anyway, because the alternative to an honest empty state is a bare
    table header floating over nothing if that invariant ever breaks.
    """
    return rx.center(
        rx.vstack(
            rx.icon("route-off", size=28, color=rx.color("gray", 8)),
            rx.text("No rides on this route.", size="2", color_scheme="gray"),
            spacing="2",
            align="center",
        ),
        padding_y="2em",
        width="100%",
    )


def route_rides_table() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text("Rides on this route", size="3", weight="bold"),
            rx.spacer(),
            rx.text(
                "What changes between passes is you, not the course.",
                size="1",
                color_scheme="gray",
            ),
            width="100%",
            align="center",
        ),
        rx.cond(
            RouteDetailState.ride_count > 0,
            rx.box(
                rx.table.root(
                    _header(),
                    rx.table.body(rx.foreach(RouteDetailState.rides, _row)),
                    variant="surface",
                    width="100%",
                ),
                # Nine columns will not fit a narrow window; scroll the table
                # rather than letting it push the page sideways.
                width="100%",
                overflow_x="auto",
            ),
            _empty_state(),
        ),
        spacing="3",
        width="100%",
        align="start",
    )
