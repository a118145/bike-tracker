"""Lap strip beneath the ride chart (masterplan §2.4.2, ``docs/image_2.png``).

Consecutive numbered bars with "Laps" written to their left. Bar widths are
proportional to lap duration, so the strip lines up with the time axis of the
chart directly above it — a lap that took a quarter of the ride is a quarter
of the strip.

Clicking a bar selects that lap and every stat card switches to its numbers;
clicking the selected bar again returns to the whole ride.
"""

import reflex as rx

from ..states.ride_detail_state import LapBar, RideDetailState


def _bar(lap: LapBar) -> rx.Component:
    selected = RideDetailState.selected_lap == lap.index
    return rx.tooltip(
        rx.box(
            rx.text(
                lap.label,
                size="1",
                weight="bold",
                color="white",
                padding_left="0.4em",
            ),
            # Percentage widths rather than flex weights: the sum is exactly
            # 100% by construction, and the gap between bars comes from the
            # border so it cannot distort the proportions.
            width=lap.width_pct.to_string() + "%",
            height="1.6em",
            display="flex",
            align_items="center",
            border_radius="0.25em",
            border="2px solid transparent",
            cursor="pointer",
            background=rx.cond(
                selected,
                rx.color("accent", 10),
                rx.color("purple", 9),
            ),
            opacity=rx.cond(selected, "1", "0.75"),
            on_click=RideDetailState.select_lap(lap.index),
            style={
                "transition": "opacity 120ms ease",
                "_hover": {"opacity": "1"},
            },
        ),
        content=f"Lap {lap.label}: " + lap.distance_km + " km · " + lap.duration,
    )


def lap_bars() -> rx.Component:
    """The lap strip, or nothing when the ride has no meaningful laps."""
    return rx.cond(
        RideDetailState.has_laps,
        rx.hstack(
            rx.text(
                "Laps",
                size="2",
                weight="medium",
                color=rx.color("purple", 11),
                min_width="3em",
            ),
            rx.hstack(
                rx.foreach(RideDetailState.laps, _bar),
                spacing="1",
                width="100%",
            ),
            width="100%",
            align="center",
            spacing="3",
        ),
    )
