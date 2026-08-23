"""Training-zone table, shared by the Fitness view and the Dashboard.

The zone bands themselves are pure arithmetic in ``metrics/zones.py``; this is
only the view model and the card that renders it. It lives in ``components/``
rather than beside either state because both pages need it, and a component
importing a dataclass from a state would invert the dependency the folder
contract sets out (AGENTS.md rule 4).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import reflex as rx

from ..metrics import Zone


@dataclass
class ZoneRow:
    """One training zone, pre-formatted for ``rx.foreach``.

    Cards render inside a foreach, where no per-item Python runs, so the range
    string has to exist before the frontend sees it.
    """

    name: str = ""
    description: str = ""
    range_text: str = ""


def zone_rows(zones: Sequence[Zone]) -> list[ZoneRow]:
    """View models for a computed band list.

    ``metrics.power_zones`` / ``hr_zones`` return an empty list for a
    non-positive reference value, so a missing FTP or LTHR yields no rows at
    all rather than a table of zeros (rule 13).
    """
    return [
        ZoneRow(name=zone.name, description=zone.description, range_text=zone.label)
        for zone in zones
    ]


def zone_row(zone: ZoneRow) -> rx.Component:
    return rx.hstack(
        rx.badge(zone.name, variant="soft", size="1"),
        rx.text(zone.description, size="1"),
        rx.spacer(),
        rx.text(zone.range_text, size="1", weight="medium"),
        width="100%",
        align="center",
        spacing="2",
    )


def zone_card(title: str, icon: str, zones: rx.Var[list[ZoneRow]], missing: str) -> rx.Component:
    """A titled card of zone bands, or a note saying why there are none."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon(icon, size=16, color=rx.color("accent", 10)),
                rx.text(title, size="2", weight="bold"),
                align="center",
                spacing="2",
                width="100%",
            ),
            rx.cond(
                zones.length() > 0,
                rx.vstack(rx.foreach(zones, zone_row), spacing="1", width="100%"),
                rx.text(missing, size="1", color_scheme="gray"),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )
