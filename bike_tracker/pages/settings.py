"""Settings view (masterplan §2.6).

A stack of sections, one per topic, each owning its own state and its own
dialogs. The page composes; it does not lay out. §2.6.3 promises more sections
later, and this is the shape that makes adding one a single import.

The OAuth callback route lives here too. It is a page rather than an API route
because Hammerhead redirects a *browser* to it, and because
``resolve_active_route`` matches segment-wise — ``/settings/hammerhead/callback``
lights up Settings in the sidebar without a nav entry of its own (rule 1).
"""

import reflex as rx

from ..components.bikes_section import bikes_section
from ..components.hammerhead_section import hammerhead_section
from ..components.tags_section import tags_section
from ..navigation import routes
from ..states.bikes_state import BikesState
from ..states.hammerhead_state import HammerheadState
from ..states.tags_state import TagsState
from ..ui.base import base_page


@rx.page(
    route=routes.SETTINGS_ROUTE,
    title="Settings",
    on_load=[BikesState.load, TagsState.load, HammerheadState.load],
)
def settings() -> rx.Component:
    return base_page(
        rx.vstack(
            rx.heading("Settings", size="7"),
            bikes_section(),
            rx.divider(),
            tags_section(),
            rx.divider(),
            hammerhead_section(),
            spacing="5",
            width="100%",
            align="start",
        )
    )


@rx.page(
    route=routes.HAMMERHEAD_CALLBACK_ROUTE,
    title="Connecting to Hammerhead",
    on_load=HammerheadState.handle_callback,
)
def hammerhead_callback() -> rx.Component:
    """Where Hammerhead sends the browser after the consent screen.

    Only reachable if the registered redirect URI points at this app. When it
    does not, the user lands somewhere else entirely and pastes the URL into
    the Settings card instead — same exchange, same code path.
    """
    return base_page(
        rx.center(
            rx.vstack(
                rx.spinner(size="3"),
                rx.text("Finishing the Hammerhead connection…", size="2", color_scheme="gray"),
                spacing="3",
                align="center",
            ),
            width="100%",
            min_height="40vh",
        )
    )
