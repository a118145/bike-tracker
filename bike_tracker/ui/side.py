"""Sidebar — the only navigation surface in this layout.

Renders a flat list: a section's ``sub_items`` if it has any, otherwise the
section itself. Sections with sub-pages become a labelled group rather than a
collapsible submenu, so every destination is always one click away.

Links are plain ``href``. No ``on_click`` — highlighting is derived from the
router, see ``navigation/state.py``.
"""

import reflex as rx

from ..navigation.nav_config import NAV_ITEMS, NavItem
from ..navigation.state import NavState

SIDEBAR_WIDTH = "16em"


def sidebar_item(label: str, icon: str, route: str) -> rx.Component:
    active = NavState.active_route == route
    return rx.link(
        rx.hstack(
            rx.icon(icon, size=17),
            rx.text(label, size="3"),
            width="100%",
            padding_x="0.6rem",
            padding_y="0.55rem",
            align="center",
            spacing="3",
            background=rx.cond(active, rx.color("accent", 5), "transparent"),
            color=rx.cond(active, rx.color("accent", 11), rx.color("gray", 12)),
            border_radius="0.5em",
            style={
                "_hover": {
                    "background": rx.color("accent", 4),
                    "color": rx.color("accent", 11),
                },
            },
        ),
        href=route,
        underline="none",
        weight="medium",
        width="100%",
    )


def _section(item: NavItem) -> rx.Component:
    """A section: its sub-items under a heading, or just the section link."""
    sub_items = item.get("sub_items", [])
    if not sub_items:
        return sidebar_item(item["label"], item["icon"], item["route"])

    return rx.vstack(
        rx.text(
            item["label"].upper(),
            size="1",
            weight="bold",
            color_scheme="gray",
            padding_x="0.6rem",
        ),
        *[sidebar_item(s["label"], s["icon"], s["route"]) for s in sub_items],
        spacing="1",
        width="100%",
        align="start",
    )


def sidebar() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.icon("box", size=22, color=rx.color("accent", 10)),
            rx.heading("Bike Tracker", size="4"),
            align="center",
            spacing="2",
            padding_x="0.6rem",
            padding_bottom="0.5em",
            width="100%",
        ),
        *[_section(item) for item in NAV_ITEMS],
        rx.spacer(),
        # No top bar in this layout, so the colour-mode toggle lives here.
        rx.hstack(
            rx.color_mode.button(),
            width="100%",
            justify="start",
            padding_x="0.3rem",
        ),
        spacing="4",
        position="fixed",
        left="0px",
        top="0px",
        padding_x="1em",
        padding_y="1.5em",
        background=rx.color("accent", 3),
        align="start",
        height="100vh",
        width=SIDEBAR_WIDTH,
        z_index="10",
    )
