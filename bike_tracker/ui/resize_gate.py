"""Mobile resize gate.

Chrome, not a routed page — it lives in ``ui/`` so ``ui.base`` can use it
without importing from ``pages/`` (which would be an import cycle, since
every page imports ``ui.base``).

Shown instead of the app on phones and tablets. This is the deliberate
exception to responsive design: the layout assumes a fixed 16em sidebar plus
a section bar, which cannot reflow into a narrow viewport usefully.

Swap this for a drawer-based responsive layout if an app needs real mobile
support.
"""

import reflex as rx


def browser_resize_page() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.icon("monitor", size=40, color=rx.color("accent", 10)),
            rx.heading("Please widen your window", size="5"),
            rx.text(
                "This application is designed for desktop screens.",
                size="2",
                color_scheme="gray",
                text_align="center",
            ),
            spacing="3",
            align="center",
        ),
        height="100vh",
        padding="2em",
    )
