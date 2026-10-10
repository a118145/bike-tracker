"""Rides list (masterplan §2.4.1).

A card per ride: borderless SVG map preview as an inset, then the ride name,
distance, elevation, ride time and date. The whole card is the link to the
detail view.
"""

import reflex as rx

from ..components.cards import card_grid
from ..components.map_view import route_preview_svg
from ..navigation import routes
from ..states.rides_state import RideCard, RideEvalBadge, RidesState
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


def _eval_badge(badge: RideEvalBadge) -> rx.Component:
    """An FTP or baseline evaluation this ride produced.

    Amber and teal are the ride-detail Power-card colours, carried over so the
    same evaluation reads the same on both pages.
    """
    return rx.badge(badge.label, variant="soft", color_scheme=badge.color_scheme)


def _ride_card(ride: RideCard) -> rx.Component:
    return rx.link(
        rx.card(
            rx.hstack(
                route_preview_svg(ride.preview_points, size="64px"),
                rx.vstack(
                    rx.hstack(
                        rx.heading(
                            ride.name,
                            size="3",
                            white_space="nowrap",
                            overflow="hidden",
                            # Ellipsis rather than a clip mid-character. The
                            # date below is `flex_shrink="0"`, so it is the
                            # name that gives way when the card runs out.
                            text_overflow="ellipsis",
                        ),
                        rx.spacer(),
                        rx.text(
                            ride.date,
                            size="1",
                            color_scheme="gray",
                            white_space="nowrap",
                            flex_shrink="0",
                        ),
                        align="baseline",
                        spacing="2",
                        width="100%",
                        min_width="0",
                    ),
                    rx.hstack(
                        # Light red when no bike could be matched, so an
                        # unassigned ride is visible at a glance in the list.
                        # `variant="soft"` is what keeps it a tint rather than
                        # an alarm.
                        rx.badge(
                            ride.bike_name,
                            variant="soft",
                            color_scheme=rx.cond(ride.bike_assigned, "gray", "red"),
                        ),
                        rx.foreach(ride.evaluations, _eval_badge),
                        rx.cond(
                            ride.excluded,
                            rx.badge(
                                rx.icon("eye-off", size=12),
                                "Excluded",
                                variant="outline",
                                color_scheme="gray",
                            ),
                        ),
                        spacing="2",
                        align="center",
                        wrap="wrap",
                    ),
                    rx.hstack(
                        _figure(ride.distance_km, "km", "Dist"),
                        _figure(ride.elevation_m, "m", "Asc"),
                        _figure(ride.duration, "", "Time"),
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
            height="100%",
            # Dimmed rather than hidden: the list is where a ride is found
            # again to be restored.
            opacity=rx.cond(ride.excluded, "0.6", "1"),
            style={"_hover": {"borderColor": rx.color("accent", 8)}},
        ),
        href=ride.detail_href,
        underline="none",
        width="100%",
    )


def _toolbar() -> rx.Component:
    return rx.hstack(
        rx.upload(
            rx.button(
                rx.icon("upload", size=16),
                "Upload FIT files",
                loading=RidesState.uploading,
                type="button",
            ),
            id="fit_upload",
            accept={"application/octet-stream": [".fit"]},
            multiple=True,
            on_drop=RidesState.handle_upload(rx.upload_files(upload_id="fit_upload")),
            border="none",
            padding="0",
        ),
        rx.button(
            rx.icon("folder-sync", size=16),
            "Scan drop folder",
            variant="soft",
            loading=RidesState.loading,
            on_click=RidesState.scan_drop_folder,
        ),
        spacing="3",
        align="center",
    )


def _empty_state() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.icon("bike", size=32, color=rx.color("gray", 8)),
            rx.text("No rides yet.", weight="medium"),
            rx.text(
                "Upload a FIT file from the Karoo, or drop files into the "
                "inbox folder and scan it.",
                size="2",
                color_scheme="gray",
                text_align="center",
                max_width="26em",
            ),
            spacing="2",
            align="center",
        ),
        padding_y="3em",
        width="100%",
    )


@rx.page(route=routes.RIDES_ROUTE, title="Rides", on_load=RidesState.load)
def rides() -> rx.Component:
    return base_page(
        rx.vstack(
            rx.hstack(
                rx.vstack(
                    rx.heading("Rides", size="7"),
                    rx.text(
                        rx.cond(
                            RidesState.has_rides,
                            RidesState.ride_count.to_string() + " ride(s) recorded.",
                            "Nothing imported yet.",
                        ),
                        size="2",
                        color_scheme="gray",
                    ),
                    spacing="1",
                    align="start",
                ),
                rx.spacer(),
                _toolbar(),
                width="100%",
                align="center",
            ),
            rx.cond(
                RidesState.has_rides,
                card_grid(rx.foreach(RidesState.rides, _ride_card)),
                _empty_state(),
            ),
            spacing="4",
            width="100%",
            align="start",
        )
    )
