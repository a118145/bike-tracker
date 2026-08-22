"""Card components.

Deliberately model-agnostic — nothing here imports from ``models/``, so this
module is usable in an app with no database at all.
"""

import reflex as rx

# Width of one list card, and the gap between them. Three cards plus two gaps
# come to 1112 px inside the 1136 px content column (Radix `--container-4`),
# so three fit with 24 px to spare. Sized deliberately short of an exact
# third: at 368 px the three would total exactly 1136 and a sub-pixel rounding
# difference would silently drop the last column onto its own row.
CARD_WIDTH = "360px"
CARD_GAP = "1em"


def card_grid(*children: rx.Component) -> rx.Component:
    """A list of cards packed into as many columns as the width allows.

    ``repeat(auto-fill, CARD_WIDTH)`` uses a *fixed* track rather than
    ``minmax()``. Fixed keeps a card at a third of the column instead of
    letting two stretch to half each; ``auto-fill`` still floors the column
    count to whatever actually fits, so 2.6 cards' worth of width gives two
    columns.

    A plain box rather than ``rx.grid``: the latter's ``columns`` prop is a
    Radix prop that only takes a fixed count, whereas unknown keyword
    arguments here land in the emotion ``css`` prop.
    """
    return rx.box(
        *children,
        display="grid",
        grid_template_columns=f"repeat(auto-fill, {CARD_WIDTH})",
        gap=CARD_GAP,
        width="100%",
    )


def stat_card(label: str, value: rx.Var | str, icon: str) -> rx.Component:
    """Small labelled metric tile.

    ``value`` is usually a state var. Non-string vars need ``.to_string()``
    before they can be rendered as text.
    """
    return rx.card(
        rx.hstack(
            rx.icon(icon, size=20, color=rx.color("accent", 10)),
            rx.vstack(
                rx.text(label, size="1", color_scheme="gray"),
                rx.heading(value, size="5"),
                spacing="0",
                align="start",
            ),
            spacing="3",
            align="center",
        ),
    )
