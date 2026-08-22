"""Card components.

Deliberately model-agnostic — nothing here imports from ``models/``, so this
module is usable in an app with no database at all.
"""

import reflex as rx


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
