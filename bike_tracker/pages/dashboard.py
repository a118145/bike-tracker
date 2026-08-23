"""Dashboard — the landing page (masterplan §2.2).

Layout order is the spec: the heat map of every ride is **above** the cards.
Below it, the summary card spans the width because it carries the two zone
tables, and the per-bike cards follow in the usual card grid.

Clicking a bike card limits the map to that bike's rides. The summary card is
deliberately inert — it is the readout, not a control.
"""

import reflex as rx

from ..components.cards import card_grid
from ..components.map_view import map_slot
from ..components.zone_table import zone_card
from ..navigation import routes
from ..states.dashboard_state import BikeSummary, DashboardState
from ..ui.base import base_page


def _stat(label: str, value: rx.Var[str], unit: str = "") -> rx.Component:
    """A small labelled figure, as on the route summary card."""
    return rx.vstack(
        rx.text(label, size="1", color_scheme="gray", white_space="nowrap"),
        rx.hstack(
            rx.heading(value, size="5", weight="medium"),
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
    """Totals over every ride, plus the current FTP and its zones.

    The totals are **not** the sum of the bike cards: a ride with no bike
    assigned counts here and appears on no card.
    """
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("sigma", size=16, color=rx.color("accent", 10)),
                rx.text("All rides", size="2", weight="bold"),
                rx.spacer(),
                rx.cond(
                    DashboardState.is_filtered,
                    rx.text(
                        "The map is filtered; these totals are not",
                        size="1",
                        color_scheme="gray",
                    ),
                ),
                width="100%",
                align="center",
                spacing="2",
            ),
            rx.grid(
                _stat("Distance", DashboardState.total_distance, "km"),
                _stat("Rides", DashboardState.total_rides),
                _stat("Routes", DashboardState.total_routes),
                _stat("Ø per ride", DashboardState.total_avg, "km"),
                _stat("Calories", DashboardState.total_calories, "kcal"),
                _stat("FTP", DashboardState.current_ftp, "W"),
                columns="6",
                spacing="3",
                width="100%",
            ),
            rx.cond(
                DashboardState.current_ftp_date != "",
                rx.text(
                    "FTP measured " + DashboardState.current_ftp_date,
                    size="1",
                    color_scheme="gray",
                ),
            ),
            rx.grid(
                zone_card("Power zones", "zap", DashboardState.power_zones, "No FTP recorded yet."),
                zone_card(
                    "Heart-rate zones",
                    "heart",
                    DashboardState.hr_zones,
                    "No LTHR recorded yet.",
                ),
                columns="2",
                spacing="3",
                width="100%",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def _bike_card(bike: BikeSummary) -> rx.Component:
    """One bike's totals, and the map filter toggle.

    Same selected styling as the clickable stat cards on ride detail, so a
    card that does something looks the same everywhere in the app.
    """
    active = DashboardState.selected_bikes.contains(bike.id)
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("bike", size=16, color=rx.color("accent", 10)),
                rx.text(bike.name, size="2", weight="bold"),
                rx.spacer(),
                rx.cond(
                    active,
                    rx.badge("Filtering", color_scheme="blue", variant="soft", size="1"),
                ),
                width="100%",
                align="center",
                spacing="2",
            ),
            rx.grid(
                _stat("Distance", bike.distance_km, "km"),
                _stat("Rides", bike.ride_count),
                _stat("Routes", bike.route_count),
                _stat("Ø per ride", bike.avg_km, "km"),
                _stat("Calories", bike.calories, "kcal"),
                columns="3",
                spacing="3",
                width="100%",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
        cursor="pointer",
        on_click=DashboardState.toggle_bike(bike.id),
        background=rx.cond(active, rx.color("accent", 3), rx.color("gray", 2)),
        border=rx.cond(
            active,
            f"1px solid {rx.color('accent', 8)}",
            f"1px solid {rx.color('gray', 5)}",
        ),
        style={
            "transition": "background 120ms ease, border-color 120ms ease",
            "_hover": {"borderColor": rx.color("accent", 8)},
        },
    )


def _no_bikes() -> rx.Component:
    return rx.callout(
        "No bike profiles yet. Add one on the Bikes page so rides can be detected and grouped.",
        icon="info",
        color_scheme="gray",
        size="1",
    )


@rx.page(route=routes.HOME_ROUTE, title="Dashboard", on_load=DashboardState.load)
def dashboard() -> rx.Component:
    return base_page(
        rx.vstack(
            rx.vstack(
                rx.heading("Dashboard", size="7"),
                rx.text(
                    "Every ride drawn together: the more often a road is "
                    "ridden, the darker it gets. Click a bike to see only its "
                    "rides.",
                    size="2",
                    color_scheme="gray",
                ),
                spacing="1",
                align="start",
            ),
            map_slot(
                DashboardState.map_figure,
                loading=DashboardState.loading,
                has_track=DashboardState.has_track,
                record_key=DashboardState.map_key,
                empty_message="No rides with a GPS track yet.",
            ),
            rx.cond(
                DashboardState.truncated_note != "",
                rx.text(DashboardState.truncated_note, size="1", color_scheme="gray"),
            ),
            rx.skeleton(_summary_card(), loading=DashboardState.loading),
            rx.cond(
                DashboardState.has_bikes,
                card_grid(rx.foreach(DashboardState.bike_cards, _bike_card)),
                _no_bikes(),
            ),
            spacing="4",
            align="start",
            width="100%",
        )
    )
