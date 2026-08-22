"""404.

Registering a page at ``/404`` is how Reflex overrides the default
not-found page — there is no ``add_custom_404_page`` API.
"""

import reflex as rx

from ..navigation import routes
from ..ui.base import base_page


@rx.page(route=routes.NOT_FOUND_ROUTE, title="404 - Not Found")
def not_found() -> rx.Component:
    return base_page(
        rx.center(
            rx.vstack(
                rx.heading("404", size="9", color=rx.color("accent", 10)),
                rx.text("That page does not exist.", size="3", color_scheme="gray"),
                rx.link(
                    rx.button(rx.icon("house", size=16), "Back home"),
                    href=routes.HOME_ROUTE,
                    margin_top="0.5em",
                ),
                spacing="3",
                align="center",
            ),
            padding_y="4em",
        )
    )
