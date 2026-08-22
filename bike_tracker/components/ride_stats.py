"""Grouped ride statistic cards (masterplan §2.4.2, ``docs/image.png``).

The grouping is the spec: each coloured block in that screenshot becomes one
card here, with the same members in the same order, and temperature grouped
with the distance figures as the masterplan requires.

Cards are declared as data rather than built by hand. There are about thirty
values across seven groups, and a table makes the grouping — the thing the
screenshot actually specifies — readable at a glance instead of buried in
layout code.

**Clickability.** A card that names a channel toggles that channel into the
y-t chart above (the switch row in ``docs/image_2.png``), changes appearance
while active, and shows a hover effect. Cards whose values have no time
series — elevation totals, energy — are inert and say so by not reacting.
"""

from dataclasses import dataclass

import reflex as rx

from ..states.ride_detail_state import RideDetailState


@dataclass(frozen=True)
class Stat:
    """One labelled value inside a card."""

    key: str  # index into RideDetailState.stats
    label: str
    unit: str = ""


@dataclass(frozen=True)
class StatGroup:
    """One card: an icon, an optional toggleable channel, and its values."""

    title: str
    icon: str
    stats: list[Stat]
    channel: str = ""  # empty = not clickable
    columns: int = 3


# Order and membership follow docs/image.png top to bottom.
STAT_GROUPS: list[StatGroup] = [
    StatGroup(
        title="Distance & time",
        icon="ruler",
        channel="temperature",
        stats=[
            Stat("distance", "Distance", "km"),
            Stat("moving_time", "Ride time"),
            Stat("paused_time", "Paused"),
            Stat("avg_temperature", "Ø Temperature", "°C"),
            Stat("max_temperature", "Max. Temperature", "°C"),
            Stat("min_temperature", "Min. Temperature", "°C"),
        ],
    ),
    StatGroup(
        title="Elevation",
        icon="mountain",
        stats=[
            Stat("elevation_gain", "Ascent", "m"),
            Stat("elevation_loss", "Descent", "m"),
            Stat("avg_vam", "Ø VAM", "m/h"),
            Stat("avg_grade", "Ø Grade", "%"),
            Stat("max_grade", "Max. Grade", "%"),
            Stat("min_grade", "Min. Grade", "%"),
            Stat("avg_altitude", "Ø Altitude", "m"),
            Stat("max_altitude", "Max. Altitude", "m"),
            Stat("min_altitude", "Min. Altitude", "m"),
        ],
    ),
    StatGroup(
        title="Speed",
        icon="gauge",
        channel="speed",
        stats=[
            Stat("avg_speed", "Ø Speed", "km/h"),
            Stat("max_speed", "Max. Speed", "km/h"),
            Stat("min_speed", "Min. Speed", "km/h"),
        ],
    ),
    StatGroup(
        title="Heart rate",
        icon="heart",
        channel="hr",
        stats=[
            Stat("avg_hr", "Ø HR", "bpm"),
            Stat("max_hr", "Max. HR", "bpm"),
            Stat("min_hr", "Min. HR", "bpm"),
        ],
    ),
    StatGroup(
        title="Cadence",
        icon="rotate-cw",
        channel="cadence",
        stats=[
            Stat("avg_cadence", "Ø Cadence", "rpm"),
            Stat("max_cadence", "Max. Cadence", "rpm"),
            Stat("min_cadence", "Min. Cadence", "rpm"),
        ],
    ),
    StatGroup(
        title="Power",
        icon="zap",
        channel="power",
        stats=[
            Stat("avg_power", "Ø Power", "W"),
            Stat("max_power", "Max. Power", "W"),
            Stat("min_power", "Min. Power", "W"),
            Stat("normalized_power", "Norm. Power", "W"),
            Stat("intensity_factor", "Int. Factor"),
            Stat("tss", "TSS"),
            Stat("energy", "Work", "kJ"),
            Stat("balance", "Ø Balance L/R"),
            Stat("ftp", "FTP", "W"),
        ],
    ),
    StatGroup(
        title="Energy",
        icon="flame",
        stats=[
            Stat("calories", "Calories", "kcal"),
            Stat("calories_per_hour", "Ø Burn", "/h"),
            Stat("decoupling", "Decoupling", "%"),
        ],
    ),
]


def _value(stat: Stat) -> rx.Component:
    """One value: a small grey label, a large number, a small unit suffix."""
    return rx.vstack(
        rx.text(stat.label, size="1", color_scheme="gray", white_space="nowrap"),
        rx.hstack(
            rx.heading(
                RideDetailState.stats.get(stat.key, "—"),
                size="5",
                weight="medium",
            ),
            rx.text(
                stat.unit,
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


def _card(group: StatGroup) -> rx.Component:
    clickable = bool(group.channel)
    # `available_channels` is what the ride actually recorded: a ride with no
    # power meter must not offer a power toggle that would do nothing.
    enabled = clickable and RideDetailState.available_channels.contains(group.channel)
    active = clickable and RideDetailState.active_channels.contains(group.channel)

    body = rx.vstack(
        rx.hstack(
            rx.icon(group.icon, size=16, color=rx.color("accent", 10)),
            rx.text(group.title, size="2", weight="bold"),
            rx.spacer(),
            rx.cond(
                active,
                rx.badge("On chart", color_scheme="blue", variant="soft", size="1"),
            ),
            align="center",
            spacing="2",
            width="100%",
        ),
        rx.grid(
            *[_value(stat) for stat in group.stats],
            columns=str(group.columns),
            spacing="3",
            width="100%",
        ),
        spacing="3",
        width="100%",
        align="start",
    )

    if not clickable:
        return rx.card(body, width="100%")

    return rx.card(
        body,
        width="100%",
        cursor=rx.cond(enabled, "pointer", "default"),
        on_click=RideDetailState.toggle_channel(group.channel),
        background=rx.cond(active, rx.color("accent", 3), rx.color("gray", 2)),
        border=rx.cond(
            active,
            f"1px solid {rx.color('accent', 8)}",
            f"1px solid {rx.color('gray', 5)}",
        ),
        opacity=rx.cond(enabled, "1", "0.55"),
        style={
            "transition": "background 120ms ease, border-color 120ms ease",
            "_hover": {"borderColor": rx.color("accent", 8)},
        },
    )


def ride_stat_cards() -> rx.Component:
    """The full grid of stat cards for the selected ride or lap."""
    return rx.vstack(
        rx.hstack(
            rx.text("Statistics", size="3", weight="bold"),
            rx.badge(RideDetailState.scope_label, variant="soft", color_scheme="gray"),
            rx.spacer(),
            rx.text(
                "Click a card to add its channel to the chart",
                size="1",
                color_scheme="gray",
            ),
            width="100%",
            align="center",
            spacing="2",
        ),
        rx.grid(
            *[_card(group) for group in STAT_GROUPS],
            columns="2",
            spacing="3",
            width="100%",
        ),
        spacing="3",
        width="100%",
        align="start",
    )
